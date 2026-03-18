#include <stdint.h>

// Internal filter state
static int last = 0;
static int32_t s1z1 = 0, s1z2 = 0;
static int32_t s2z1 = 0, s2z2 = 0;
static int32_t s3z1 = 0, s3z2 = 0;
static int32_t s4z1 = 0, s4z2 = 0;

// User-controlled strength (0–100)
static int strength = 50;

__declspec(dllexport)
void set_strength(int s)
{
    if (s < 0) s = 0;
    if (s > 100) s = 100;
    strength = s;
}

__declspec(dllexport)
void process(int16_t* in, int n_samples, int16_t* out)
{
    // Base filter coefficients (scaled by 2^14)
    const int B0 = 1974, B1 = 3948, B2 = 1974;
    const int A1 = -15871;

    // A2 mapping (controls high-frequency response)
    const int baseA2 = 6000;
    const int maxA2  = 10200;
    const int midA2  = (baseA2 + maxA2) / 2;

    int A2 = baseA2 + (maxA2 - baseA2) * strength / 100;

    // Delta from "neutral" point (for gain compensation)
    int delta = A2 - midA2;

    int o = 0;

    for (int i = 0; i < n_samples; i++) {
        int32_t c_s = in[i];

        // ---- 4x interpolated samples (linear, clean) ----
        int32_t s0 = last;
        int32_t s1 = (last * 3 + c_s) >> 2;
        int32_t s2 = (last + c_s) >> 1;
        int32_t s3 = (last + c_s * 3) >> 2;

        int32_t samples[4] = {s0, s1, s2, s3};

        for (int j = 0; j < 4; j++) {
            int32_t smp = samples[j];

            // ---- Filter chain ----
            int32_t v1 = (smp * B0 + s1z1) >> 14;
            s1z1 = smp * B1 - A1 * v1 + s1z2;
            s1z2 = smp * B2 - A2 * v1;

            int32_t v2 = (v1 * B0 + s2z1) >> 14;
            s2z1 = v1 * B1 - A1 * v2 + s2z2;
            s2z2 = v1 * B2 - A2 * v2;

            int32_t v3 = (v2 * B0 + s3z1) >> 14;
            s3z1 = v2 * B1 - A1 * v3 + s3z2;
            s3z2 = v2 * B2 - A2 * v3;

            int32_t v4 = (v3 * B0 + s4z1) >> 14;
            s4z1 = v3 * B1 - A1 * v4 + s4z2;
            s4z2 = v3 * B2 - A2 * v4;

            // ---- Base gain (~+25%, your original sweet spot) ----
            int32_t gain = v4 + (v4 >> 2);

            // ---- Subtle dynamic compensation ----
            // Adjust gain slightly depending on treble boost
            // Keeps loudness more consistent across slider range
            gain = gain + ((gain * delta) >> 12);

            v4 = gain;

            // ---- Clamp to 16-bit range ----
            if (v4 > 32767) v4 = 32767;
            if (v4 < -32768) v4 = -32768;

            out[o++] = (int16_t)v4;
        }

        last = c_s;
    }
}

__declspec(dllexport)
void reset()
{
    // Reset all filter states to avoid artifacts (clicks)
    last = 0;
    s1z1 = s1z2 = 0;
    s2z1 = s2z2 = 0;
    s3z1 = s3z2 = 0;
    s4z1 = s4z2 = 0;
}