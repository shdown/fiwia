#include <string.h>
#include <stdio.h>
#include <stdint.h>

static int test_bmi2(void)
{
    fprintf(stderr, "Testing mulx (BMI2)...\n");

    uint64_t src = UINT64_C(7319390473219274092);
    uint64_t d = UINT64_C(13125557717734106714);
    uint64_t dst_lo;
    uint64_t dst_hi;
    asm volatile (
        "mulx %[s], %[dlo], %[dhi]\n"
        : [dlo] "=r" (dst_lo), [dhi] "=r" (dst_hi)
        : "d" (d), [s] "r" (src)
        : /*no clobbers*/
    );
    if (dst_lo == UINT64_C(17404240107838263288) && dst_hi == UINT64_C(5208023797098915650)) {
        fprintf(stderr, "mulx is supported.\n");
        return 0;
    } else {
        fprintf(stderr, "mulx gave unexpected results.\n");
        return 1;
    }
}

static int test_adx(void)
{
    fprintf(stderr, "Testing adcx (ADX)...\n");

    uint64_t src = UINT64_C(7319390473219274092);
    uint64_t dst = UINT64_C(13125557717734106714);
    asm volatile (
        "stc\n"
        "adcxq %[src], %[dst]\n"
        : [dst] "+r" (dst)
        : [src] "r" (src)
        : "cc"
    );
    if (dst == UINT64_C(1998204117243829191)) {
        fprintf(stderr, "adcx is supported.\n");
        return 0;
    } else {
        fprintf(stderr, "adcx gave unexpected result.\n");
        return 1;
    }
}

static int test_avx(void)
{
    fprintf(stderr, "Testing vmovdqu (AVX)...\n");
    uint64_t words[4] = {1, 2, 3, 4};
    asm volatile (
        "vmovdqu (%[ptr]), %%ymm0\n"
        : /*no outputs*/
        : [ptr] "r" ((uint64_t *) words)
        : "ymm0", "cc", "memory"
    );
    fprintf(stderr, "vmovdqu seems to be supported (we were not killed with SIGILL).\n");
    return 0;
}

static int test_avx2(void)
{
    fprintf(stderr, "Testing vpslldq (AVX2)...\n");
    uint64_t words[4] = {1, 2, 3, 4};
    asm volatile (
        "vmovdqu (%[ptr]), %%ymm0\n"
        "vpslldq $1, %%ymm0, %%ymm0\n"
        "vmovdqu %%ymm0, (%[ptr])\n"
        : /*no outputs*/
        : [ptr] "r" ((uint64_t *) words)
        : "ymm0", "cc", "memory"
    );
    if (words[0] == 256 && words[1] == 512 && words[2] == 768 && words[3] == 1024) {
        fprintf(stderr, "vpslldq is supported.\n");
        return 0;
    } else {
        fprintf(stderr, "vpslldq gave unexpected result.\n");
        return 1;
    }
}

static void print_usage(void)
{
    fprintf(stderr, "USAGE: check_cap {bmi2 | adx | avx | avx2}\n");
}

int main(int argc, char **argv)
{
    if (argc != 2) {
        print_usage();
        return 2;
    }
    const char *arg = argv[1];

    if (strcmp(arg, "bmi2") == 0) {
        return test_bmi2();
    }

    if (strcmp(arg, "adx") == 0) {
        return test_adx();
    }

    if (strcmp(arg, "avx") == 0) {
        return test_avx();
    }

    if (strcmp(arg, "avx2") == 0) {
        return test_avx2();
    }

    print_usage();
    return 2;
}
