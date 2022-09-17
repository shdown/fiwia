all: asm_4.s asm_4.h asm_5.s asm_5.h inline_asm_4.h inline_asm_5.h

asm_%.s: ./gen_asm.py
	./gen_asm.py gen_asm $* > $@

asm_%.h: ./gen_asm.py
	./gen_asm.py gen_c_header $* > $@

inline_asm_%.h: ./gen_asm.py
	./gen_asm.py gen_inline_asm $* > $@

clean:
	$(RM) asm_4.s asm_4.h inline_asm_4.h asm_5.s asm_5.h inline_asm_5.h

.PHONY: all clean
