// [CGC 2026-09-29] Metal counter-sampling capability probe. ANSWERS A GATE, NOT A QUESTION OF
// PERFORMANCE: can this GPU time a single compute dispatch?
//
// WHY THIS EXISTS. Per-dispatch GPU time is the one number this engine still cannot produce, and it
// is what "does map0 cost anything on a decode step?" needs (docs/…_2026-09-28.md §22). The
// 2026-09-18 round established why the two existing instruments stop short:
//   * the per-command-buffer route (`GPUStartTime`/`GPUEndTime`) is a GROUP of nodes, and shrinking
//     the groups by raising n_cb DEADLOCKS at model load -- Metal throttles command-buffer creation
//     (~64 in flight), so the granularity is capped by Metal, not by the timestamp API;
//   * that round therefore named MTLCounterSampleBuffer as the remaining route, and explicitly
//     RETRACTED its earlier "no MTLCounterSampleBuffer needed" reading
//     (scripts/check/decode_sweep.py, the "en-nodes" / "en-fine-*" arms).
// Nobody had since checked whether this GPU can sample at a dispatch boundary AT ALL. This probe
// does, so that ~200 lines of sampling code do not get written against a closed door.
//
// RESULT ON THIS BOX (Apple M4, applegpu_g16g, macOS 15.2 SDK): THE DOOR IS CLOSED, TWICE OVER.
//   1. supportsCounterSampling(AtDispatchBoundary) == 0  -- and also AtDraw/AtTileDispatch/AtBlit.
//      Only AtStageBoundary reads 1.
//   2. Trusting that 1 is a trap: calling sampleCountersInBuffer:atSampleIndex:withBarrier: on a
//      compute encoder does not return an error, it ABORTS THE PROCESS --
//        failed assertion `MTLComputeCommandEncoder:sampleCountersInBuffer:atSampleIndex:withBarrier
//        not supported on this device'
//      The capability flag and the callable API disagree on this device, so a probe must ATTEMPT
//      the call, not read the flag.
//
// THE SUBPROCESS PATTERN. Because the failure mode is a fatal assertion, this probe re-executes
// itself as a CHILD and reports the child's status. The child's crash IS the datum, and the parent
// survives to print the whole report.
//
// WHAT IS LEFT, and now measured rather than assumed. `MTLCommandBuffer.GPUStartTime/GPUEndTime` is
// in SECONDS, and it shares a base with `MTLDevice.sampleTimestamps` (measured: the buffer started
// 0.6 ms after the sample in the same run). It does NOT share the CPU's clock: the same run reads
// `mach_absolute_time` 25 326 s away. And `sampleTimestamps` returns an IDENTICAL cpu/gpu pair on
// this device, so it does not bridge the two clocks either. Consequence for the engine: §19's
// `gap`/`union` are safe as DIFFERENCES of GPU timestamps (they never needed a common clock), but
// they cannot be placed on the CPU timeline -- the cheap route to that is closed too.
//
// WHAT IT DOES NOT DO. No engine, no model, no op. Its output is a capability report.
//
// BUILD + RUN (no build-system change, ~1s):
//   clang -fobjc-arc -O2 -framework Foundation -framework Metal \
//         scripts/check/metal_counter_probe.m -o /tmp/cgc_metal_probe && /tmp/cgc_metal_probe
//
// EXIT: 0 if per-dispatch sampling is usable, 1 if it is unavailable (a precondition a caller can
// test).

#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <mach/mach_time.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char * g_msl =
    "#include <metal_stdlib>\n"
    "using namespace metal;\n"
    // Controllable work so a dispatch is far above any counter resolution.
    "kernel void cgc_probe_busy(device float * out [[buffer(0)]],\n"
    "                            constant int & iters [[buffer(1)]],\n"
    "                            uint gid [[thread_position_in_grid]]) {\n"
    "    float x = (float)gid;\n"
    "    for (int i = 0; i < iters; ++i) { x = fma(x, 1.0000001f, 0.25f); }\n"
    "    out[gid] = x;\n"
    "}\n";

// =================================================================================================
// THE ATTEMPT. Runs in the CHILD, because at least one line below can abort the process.
// Three compute encoders in one command buffer, one sample at each encoder boundary. If the samples
// come back monotonic and region 2 is 32x region 1 (the known work ratio), the readback is real.
// =================================================================================================
static int try_stage_sampling(void) {
    @autoreleasepool {
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        if (dev == nil) { printf("CHILD: no device\n"); return 1; }
        id<MTLCounterSet> ts_set = nil;
        for (id<MTLCounterSet> s in dev.counterSets) {
            if ([s.name isEqualToString:MTLCommonCounterSetTimestamp]) { ts_set = s; }
        }
        if (ts_set == nil) { printf("CHILD: no timestamp counter set\n"); return 1; }

        NSError * err = nil;
        id<MTLLibrary> lib = [dev newLibraryWithSource:[NSString stringWithUTF8String:g_msl]
                                              options:nil error:&err];
        id<MTLFunction> fn = [lib newFunctionWithName:@"cgc_probe_busy"];
        id<MTLComputePipelineState> pso = [dev newComputePipelineStateWithFunction:fn error:&err];
        id<MTLBuffer> out = [dev newBufferWithLength:1024*sizeof(float) options:MTLResourceStorageModeShared];
        id<MTLCommandQueue> q = [dev newCommandQueue];
        if (pso == nil || q == nil) { printf("CHILD: setup failed\n"); return 1; }

        MTLCounterSampleBufferDescriptor * d = [MTLCounterSampleBufferDescriptor new];
        d.counterSet  = ts_set;
        d.storageMode = MTLStorageModeShared;
        d.sampleCount = 8;
        id<MTLCounterSampleBuffer> sb = [dev newCounterSampleBufferWithDescriptor:d error:&err];
        if (sb == nil) {
            printf("CHILD: sample buffer create FAILED: %s\n",
                   err ? err.localizedDescription.UTF8String : "?");
            return 1;
        }

        MTLSize threads = MTLSizeMake(1024, 1, 1);
        MTLSize tptg    = MTLSizeMake(256, 1, 1);
        int it_small = 2048, it_big = 65536;

        id<MTLCommandBuffer> cb = [q commandBuffer];
        for (int k = 0; k < 3; ++k) {
            id<MTLComputeCommandEncoder> e = [cb computeCommandEncoder];
            [e setComputePipelineState:pso];
            [e setBuffer:out offset:0 atIndex:0];
            int it = (k == 1) ? it_big : it_small;
            [e setBytes:&it length:sizeof(it) atIndex:1];
            // >>> THE LINE THAT FAILS ON THIS DEVICE <<<
            [e sampleCountersInBuffer:sb atSampleIndex:(NSUInteger)k withBarrier:YES];
            [e dispatchThreads:threads threadsPerThreadgroup:tptg];
            [e endEncoding];
        }
        [cb commit];
        [cb waitUntilCompleted];
        if (cb.status != MTLCommandBufferStatusCompleted) {
            printf("CHILD: cb status=%d\n", (int)cb.status);
            return 1;
        }
        NSData * data = [sb resolveCounterRange:NSMakeRange(0, 8)];
        if (data == nil || data.length < 3 * sizeof(uint64_t)) {
            printf("CHILD: resolve gave %lu bytes\n", (unsigned long)data.length);
            return 1;
        }
        const MTLCounterResultTimestamp * ts = (const MTLCounterResultTimestamp *)data.bytes;
        const uint64_t a = ts[1].timestamp - ts[0].timestamp;
        const uint64_t b = ts[2].timestamp - ts[1].timestamp;
        printf("CHILD: t0=%llu t1=%llu t2=%llu stage1=%llu stage2=%llu workRatio=%.3f\n",
               (unsigned long long)ts[0].timestamp, (unsigned long long)ts[1].timestamp,
               (unsigned long long)ts[2].timestamp,
               (unsigned long long)a, (unsigned long long)b,
               a ? (double)b / (double)a : 0.0);
        printf("CHILD: OK\n");
        return 0;
    }
}

static const char * ptname(int i) {
    switch (i) {
        case MTLCounterSamplingPointAtStageBoundary:        return "AtStageBoundary";
        case MTLCounterSamplingPointAtDrawBoundary:         return "AtDrawBoundary";
        case MTLCounterSamplingPointAtDispatchBoundary:     return "AtDispatchBoundary";
        case MTLCounterSamplingPointAtTileDispatchBoundary: return "AtTileDispatchBoundary";
        case MTLCounterSamplingPointAtBlitBoundary:         return "AtBlitBoundary";
        default: return "?";
    }
}

int main(int argc, char ** argv) {
    if (argc > 1 && strcmp(argv[1], "--try-stage") == 0) {
        return try_stage_sampling();
    }

    @autoreleasepool {
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        if (dev == nil) { printf("PROBE: RESULT=FAIL noMetalDevice\n"); return 1; }
        printf("PROBE: device=%s\n", dev.name.UTF8String);
        if (@available(macOS 14.0, *)) { printf("PROBE: architecture=%s\n", dev.architecture.name.UTF8String); }
        printf("PROBE: unifiedMemory=%d\n", (int)dev.hasUnifiedMemory);

        // --- 1. the flags ---------------------------------------------------------------------
        int sup[5];
        for (int i = 0; i < 5; ++i) {
            sup[i] = (int)[dev supportsCounterSampling:(MTLCounterSamplingPoint)i];
            printf("PROBE: supportsCounterSampling(%s)=%d\n", ptname(i), sup[i]);
        }
        const int dispatch_flag = sup[MTLCounterSamplingPointAtDispatchBoundary];

        // --- 2. counter sets ------------------------------------------------------------------
        NSArray<id<MTLCounterSet>> * sets = dev.counterSets;
        printf("PROBE: counterSets=%d\n", (int)sets.count);
        int has_ts = 0;
        for (id<MTLCounterSet> s in sets) {
            printf("PROBE:   set=%s counters=%d\n", s.name.UTF8String, (int)s.counters.count);
            if ([s.name isEqualToString:MTLCommonCounterSetTimestamp]) { has_ts = 1; }
        }
        printf("PROBE: hasTimestampSet=%d\n", has_ts);

        // --- 3. ATTEMPT, in a child, because the failure mode is a fatal assertion -------------
        char cmd[4096];
        snprintf(cmd, sizeof(cmd), "\"%s\" --try-stage 2>&1", argv[0]);
        FILE * f = popen(cmd, "r");
        int child_ok = 0;
        if (f == NULL) {
            printf("PROBE: WARN cannotLaunchChild\n");
        } else {
            char line[1024];
            while (fgets(line, sizeof(line), f) != NULL) {
                // verbatim: on failure this line IS the evidence (the driver's assertion text)
                printf("PROBE: childOutput| %s", line);
            }
            const int rc = pclose(f);
            child_ok = (rc == 0);
            printf("PROBE: childExit=%d\n", rc);
        }

        // --- 4. SELFCHECK clockBase: WHICH CPU clock is GPUStartTime on? ------------------------
        // The first revision of this check compared against mach_absolute_time and missed by ~7
        // hours, which is the machine's SLEEP time -- i.e. the base is mach_continuous_time, not
        // mach_absolute_time. Both are tested here so the answer is measured, not assumed. This
        // matters beyond curiosity: if GPU timestamps share the continuous-time base, the GPU
        // timeline can be aligned with the CPU-side submit/cb/wait stamps that §19 uses, which is
        // the instrument §19 recorded as MISSING.
        {
            uint64_t cpu_ts = 0, gpu_ts = 0;
            [dev sampleTimestamps:&cpu_ts gpuTimestamp:&gpu_ts];
            id<MTLCommandQueue> q = [dev newCommandQueue];
            id<MTLCommandBuffer> cb = [q commandBuffer];
            const uint64_t t_abs  = mach_absolute_time();
            const uint64_t t_cont = mach_continuous_time();
            [cb commit];
            [cb waitUntilCompleted];
            const double gpu_ns = cb.GPUStartTime * 1e9;
            printf("PROBE: SELFCHECK clockBase gpuStart_ns=%.0f sampleTimestamps(cpu=%llu gpu=%llu)\n",
                   gpu_ns, (unsigned long long)cpu_ts, (unsigned long long)gpu_ts);
            printf("PROBE: SELFCHECK clockBase gpuStartMinusSampleTsCpuMs=%.3f  (small => same base; "
                   "also note sampleTs.cpu==gpu, so it does NOT bridge the two clocks)\n",
                   (gpu_ns - (double)cpu_ts) / 1e6);
            printf("PROBE: SELFCHECK clockBase gpuStartMinusMachAbsoluteMs=%.3f "
                   "machAbsVsMachContMs=%.3f  (large => GPUStartTime is NOT on the CPU mach clock)\n",
                   (gpu_ns - (double)t_abs) / 1e6,
                   ((double)t_cont - (double)t_abs) / 1e6);
        }

        // --- 5. verdict -----------------------------------------------------------------------
        printf("PROBE: flag_dispatchBoundary=%d child_attempt_succeeded=%d\n", dispatch_flag, child_ok);
        if (child_ok) {
            printf("PROBE: RESULT=OK perDispatchSamplingUsable\n");
            return 0;
        }
        printf("PROBE: RESULT=UNAVAILABLE per-dispatch GPU time cannot be sampled on this device; "
               "the flag reads %d and the call aborts\n", dispatch_flag);
        return 1;
    }
}
