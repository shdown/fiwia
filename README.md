Overview
===

The `gen_asm.py` script generates either:
 - a standalone assembler listing (AT&T syntax, assumes GNU assembler),
 - a C header for a standalone assembler listing, or,
 - a C header with inline GCC-style inline assembly.

It assumes x86-64 architecture with 64-bit limbs.

It requires the **width** in limbs as a parameter.

All functions generated have `asm_${operation}_${width}` name.

Usage
===

`gen_asm.py {gen_asm | gen_c_header | gen_inline_asm} <WIDTH> [<FUNC_FILTER>]`

Examples
---
 * `./gen_asm.py gen_inline_asm 4 > asm_4.h`
 * `./gen_asm.py gen_asm 4 > asm_4.s && ./gen_asm.py gen_c_header 4 > asm_4.h`
 * `./gen_asm.py gen_inline_asm 8 asm_add_8,asm_sub_8 > asm_addsub_8.h`

Functions
===

* `uint64_t asm_add_${W}(uint64_t *a, const uint64_t *b)`

  Adds `{b, W}` to `{a, W}`. Returns carry, either 0 or `(uint64_t) -1`.

* `uint64_t asm_sub_${W}(uint64_t *a, const uint64_t *b)`

  Subtracts `{b, W}` from `{a, W}`. Returns borrow, either 0 or `(uint64_t) -1`.

* `uint64_t asm_add_masked_${W}(uint64_t *a, const uint64_t *b, uint64_t mask)`

  If `mask` is zero, does nothing and returns zero.

  If `mask` is `(uint64_t) -1`, adds `{b, W}` to `{a, W}` and returns carry, either 0 or `(uint64_t) -1`.

  Otherwise, the behavior is undefined.

* `uint64_t asm_sub_masked_${W}(uint64_t *a, const uint64_t *b, uint64_t mask)`

  If `mask` is zero, does nothing and returns zero.

  If `mask` is `(uint64_t) -1`, subtracts `{b, W}` from `{a, W}` and returns borrow, either 0 or `(uint64_t) -1`.

  Otherwise, the behavior is undefined.

* `uint64_t asm_negate_${W}(const uint64_t *a, uint64_t *b)`

  Calculates zero minus `{a, W}`, writing the result into `{b, W}`. Returns borrow, either 0 or `(uint64_t) -1`.

* `uint64_t asm_add_q_${W}(uint64_t *a, uint64_t b)`

  Adds `b` to `{a, W}`. Returns carry, either 0 or `(uint64_t) -1`.

* `uint64_t asm_sub_q_${W}(uint64_t *a, uint64_t b)`

  Subtracts `b` from `{a, W}`. Returns borrow, either 0 or `(uint64_t) -1`.

* `uint64_t asm_cmplt_${W}(const uint64_t *a, const uint64_t *b)`

  Returns `(uint64_t) -1` if `{a, W}` < `{b, W}` (as unsigned integers), zero otherwise.

* `uint64_t asm_cmple_${W}(const uint64_t *a, const uint64_t *b)`

  Returns `(uint64_t) -1` if `{a, W}` <= `{b, W}` (as unsigned integers), zero otherwise.

* `uint64_t asm_S_cmplt_${W}(const uint64_t *a, const uint64_t *b)`

  Returns 1 if `{a, W}` < `{b, W}` (as signed integers), zero otherwise.

* `uint64_t asm_S_cmple_${W}(const uint64_t *a, const uint64_t *b)`

  Returns 1 if `{a, W}` <= `{b, W}` (as signed integers), zero otherwise.

* `uint64_t asm_cmpeq_${W}(const uint64_t *a, const uint64_t *b)`

  Returns `(uint64_t) -1` if `{a, W}` == `{b, W}`, zero otherwise.

  **NOTE**: fiwia doesn't generate SIMD instructions, and this is the only function that can benefit from using them.
  The implementation is thus suboptimal and this function is only included for completeness.
  You can probably get speedup by rewriting it in C in the following way:
  ```
  uint64_t asm_cmpeq_${W}(const uint64_t *a, const uint64_t *b)
  {
      uint64_t r = 0;
      for (int i = 0; i < W; ++i)
          r |= (a[i] ^ b[i]);
      // r = r ? 0 : -1;
      asm (
          "subq $1, %[r]\n"
          "sbbq %[r], %[r]\n"
          : [r] "+r" (r)
          : /*no inputs*/
          : "cc"
      );
      return r;
  }
  ```
  Both gcc and clang are smart enough to vectorize the loop.
  All x86-64 processors support SSE2; be sure to tell your compiler whether your hardware supports AVX, AVX-512 or another extensions.

* `uint64_t asm_mul_q_${W}(const uint64_t *a, uint64_t b, uint64_t *c)`

  Multiplies `{a, W}` by `b`, writing the result without the most significant limb to `{c, W}` and returning the most significant limb of the result.

* `uint64_t asm_div_q_${W}(const uint64_t *a, uint64_t b, uint64_t *c)`

  Divides `{a, W}` by `b`, writing the result into `{c, W}` and returning the remainder. If `b` is zero, the behavior is undefined.

  **WARNING**: naïve and leaky.

* `uint64_t asm_mod_q_${W}(const uint64_t *a, uint64_t b)`

  Divides `{a, W}` by `b`, returning the remainder. If `b` is zero, the behavior is undefined.

  **WARNING**: naïve and leaky.

* `void asm_mul_lo_${W}(const uint64_t *a, const uint64_t *b, uint64_t *c)`

  Multiplies `{a, W}` by `{b, W}`, writing the lower half of the result into `{c, W}`.

* `void asm_mul_${W}(const uint64_t *a, const uint64_t *b, uint64_t *c)`

  Multiplies `{a, W}` by `{b, W}`, writing the result into `{c, 2*W}`.

* `void asm_shr_${W}(const uint64_t *a, uint64_t n, uint64_t *c)`

  Performs unsigned right shift of `{a, W}` by `n` bits, writing the result into `{c, W}`.

  If `n` >= 64, the behavior is undefined.

* `void asm_S_shr_${W}(const uint64_t *a, uint64_t n, uint64_t *c)`

  Performs signed right shift of `{a, W}` by `n` bits, writing the result into `{c, W}`.

  If `n` >= 64, the behavior is undefined.

* `void asm_shl_${W}(const uint64_t *a, uint64_t n, uint64_t *c)`

  Performs left shift of `{a, W}` by `n` bits, writing the result into `{c, W}`.

  If `n` >= 64, the behavior is undefined.

* `void asm_shr_nz_${W}(const uint64_t *a, uint64_t n, uint64_t *c)`

  Performs unsigned right shift of `{a, W}` by `n` bits, writing the result into `{c, W}`.

  If `n` >= 64 or `n == 0`, the behavior is undefined.

* `void asm_S_shr_nz_${W}(const uint64_t *a, uint64_t n, uint64_t *c)`

  Performs signed right shift of `{a, W}` by `n` bits, writing the result into `{c, W}`.

  If `n` >= 64 or `n == 0`, the behavior is undefined.

* `void asm_shl_nz_${W}(const uint64_t *a, uint64_t n, uint64_t *c)`

  Performs left shift of `{a, W}` by `n` bits, writing the result into `{c, W}`.

  If `n` >= 64 or `n == 0`, the behavior is undefined.

* `void asm_shr_words_${W}(const uint64_t *a, uint64_t n, uint64_t *c)`

  Performs unsigned right shift of `{a, W}` by `(n * 64)` bits, writing the result into `{c, W}`.

  If `n >= W`, the result is zero.

* `void asm_S_shr_words_${W}(const uint64_t *a, uint64_t n, uint64_t *c)`

  Performs signed right shift of `{a, W}` by `(n * 64)` bits, writing the result into `{c, W}`.

  If `n >= W`, the result is `W` words filled with the sign bit of `{a, W}`.

* `void asm_shl_words_${W}(const uint64_t *a, uint64_t n, uint64_t *c)`

  Performs left shift of `{a, W}` by `(n * 64)` bits, writing the result into `{c, W}`.

  If `n >= W`, the result is zero.

Definitions
---

* Naïve: implemented in the dumbest way possible, thus relatively slow (compared to a hypothetical non-naïve implementation).
* Leaky: susceptible to side-channel attacks (timing attack, etc), thus not suitable for application in cryptography.
