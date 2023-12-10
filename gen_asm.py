#!/usr/bin/env python3
import os
import sys
import subprocess
from typing import List, Union, Optional, Tuple


class RegList:
    def __init__(self, names: List[str]):
        self._names = names

    def name_by_index(self, index: int) -> str:
        return self._names[index]

    def index_by_name(self, name: str) -> int:
        return self._names.index(name)

    def names(self) -> List[str]:
        return self._names

    def __len__(self) -> int:
        return len(self._names)


SCRATCH_REGS = RegList('rax rdi rsi rdx rcx r8 r9 r10 r11'.split())


CALLEE_SAVED_REGS = RegList('rbx r12 r13 r14 r15'.split())


ALL_REGS = RegList(SCRATCH_REGS.names() + CALLEE_SAVED_REGS.names())


# First 6 function args are passed in: rdi rsi rdx rcx r8 r9
SYSV_ABI_ARG_REGS = RegList('rdi rsi rdx rcx r8 r9'.split())


class Reg:
    pass


class RealReg(Reg):
    def __init__(self, index: int):
        self.index = index

    def __str__(self):
        return f'%{ALL_REGS.name_by_index(self.index)}'

    def e_part(self):
        base_name = str(self).lstrip('%r')
        if base_name.isdigit():
            # %r8 -> r8d
            return f'%r{base_name}d'
        else:
            # %rax -> %eax
            return f'%e{base_name}'

    def l_part(self):
        base_name = str(self).lstrip('%r')
        if base_name.isdigit():
            # %r8 -> r8b
            return f'%r{base_name}b'
        else:
            if 'x' in base_name:
                # %rax -> %al
                return f'%{base_name.rstrip("x")}l'
            else:
                # %rdi -> %dil
                return f'%{base_name}l'


class FakeReg(Reg):
    def __init__(self, keyword: str):
        self.keyword = keyword

    def __str__(self):
        return f'![{self.keyword}]'

    def e_part(self):
        return f'!k[{self.keyword}]'

    def l_part(self):
        return f'!b[{self.keyword}]'


class NoVacantReg(BaseException):
    pass


class RegStore:
    def __init__(self, reg_list: RegList=SCRATCH_REGS):
        self.free_indices = [ALL_REGS.index_by_name(name) for name in reg_list.names()]
        self.writes = set()

    def _set_reg_mode(self, reg: RealReg, write: bool) -> None:
        if write:
            self.writes.add(reg.index)

    def set_mode_by_name(self, reg_name: str, write: bool) -> None:
        if write:
            index = ALL_REGS.index_by_name(reg_name)
            self.writes.add(index)

    def take(self, write: bool) -> RealReg:
        # TODO this heuristic works for now, but could be made configurable
        where = -1
        try:
            reg = RealReg(self.free_indices.pop(where))
        except IndexError:
            raise NoVacantReg()
        self._set_reg_mode(reg, write=write)
        return reg

    def untake(self, reg: RealReg) -> None:
        self.free_indices.append(reg.index)
        self.free_indices.sort()

    def take_by_index(self, index: int, write: bool) -> RealReg:
        self.free_indices.remove(index)
        reg = RealReg(index)
        self._set_reg_mode(reg, write=write)
        return reg

    def take_by_name(self, name: str, write: bool) -> RealReg:
        reg = self.take_by_index(ALL_REGS.index_by_name(name), write=write)
        self._set_reg_mode(reg, write=write)
        return reg

    def clobbers(self) -> List[str]:
        return [ALL_REGS.name_by_index(index) for index in self.writes]


class AnyPointerReg:
    pass


class PointerReg(AnyPointerReg):
    def __init__(self, reg: Reg, offset: int=0):
        self.reg = reg
        self.offset = offset

    def __str__(self):
        if self.offset:
            return f'{self.offset * 8}({self.reg})'
        else:
            return f'({self.reg})'

    def displace(self, offset: int):
        return PointerReg(reg=self.reg, offset=self.offset + offset)


class Emitter:
    pass


class SysvAbiFunctionEmitter(Emitter):
    label_counter = 0

    def __init__(self):
        self.reg_store = RegStore()
        self.fixed_regs = []
        self.arg_map = SYSV_ABI_ARG_REGS.names()

    def add_fixed_reg(self, reg_name: str) -> None:
        self.fixed_regs.append(reg_name)

    def take_zero_reg(self) -> Reg:
        reg = self.reg_store.take(write=True)
        self.emit(f'xorl {reg.e_part()}, {reg.e_part()}')
        return reg

    def set_nargs(self, nargs: int) -> None:
        self.arg_map = []
        regs_taken = []
        for i in range(nargs):
            reg_name = SYSV_ABI_ARG_REGS.name_by_index(i)
            if reg_name in self.fixed_regs:
                dst_reg = self.reg_store.take(write=True)
                self.arg_map.append(
                    str(dst_reg).lstrip('%')
                )
                self.emit(f'movq %{reg_name}, {dst_reg}')
                regs_taken.append(dst_reg)
            else:
                self.arg_map.append(reg_name)
        for reg in regs_taken:
            self.reg_store.untake(reg)

    def take_arg_reg(self, index: int, write: bool, into_reg_name: Optional[str]=None) -> Reg:
        reg_name = self.arg_map[index]
        should_move = (reg_name in self.fixed_regs) or (into_reg_name is not None and into_reg_name != reg_name)
        if should_move:
            src_reg = self.reg_store.take_by_name(reg_name, write=False)
            if into_reg_name is not None:
                dst_reg = self.reg_store.take_by_name(into_reg_name, write=True)
            else:
                dst_reg = self.reg_store.take(write=True)
            self.emit(f'movq {src_reg}, {dst_reg}')
            self.reg_store.untake(src_reg)
            return dst_reg
        else:
            return self.reg_store.take_by_name(reg_name, write=write)

    def take_retval_reg(self, may_overwrite_taken: bool=True) -> Reg:
        return self.reg_store.take_by_name('rax', write=True)

    def write_retval(self, src_reg: Reg) -> None:
        self.reg_store.set_mode_by_name('rax', write=True)
        if str(src_reg) != '%rax':
            self.emit(f'movq {src_reg}, %rax')

    def emit_prologue(self) -> None:
        pass

    def emit(self, line: str) -> None:
        print(line)

    def emit_epilogue(self) -> None:
        pass

    def gen_label(self) -> str:
        self.__class__.label_counter += 1
        return f'.L{self.__class__.label_counter}'

    def label_here(self, label: str) -> None:
        self.emit(f'{label}:')


REG_NAMES_TO_LETTERS = {
    'rax': 'a',
    'rbx': 'b',
    'rcx': 'c',
    'rdx': 'd',
    'rsi': 'S',
    'rdi': 'D',
}


class InlineAsmEmitter(Emitter):
    def __init__(self):
        self.reg_store = RegStore()
        self.args = []
        self.retval = None
        self.retval_earlyclobber = False
        self.needs_zero_input = False
        self.label_counter = 0

    def add_fixed_reg(self, reg_name: str) -> None:
        pass

    def set_nargs(self, nargs: int) -> None:
        pass

    def take_zero_reg(self) -> FakeReg:
        self.needs_zero_input = True
        return FakeReg('zero')

    def take_arg_reg(self, index: int, write: bool, into_reg_name: Optional[str]=None) -> FakeReg:
        if len(self.args) != index:
            raise ValueError('wrong order of arg indices')
        self.args.append((write, into_reg_name or ''))
        return FakeReg(f'arg{index}')

    def take_retval_reg(self, may_overwrite_taken: bool=True) -> FakeReg:
        self.retval = ''
        self.retval_earlyclobber = not may_overwrite_taken
        return FakeReg('ret')

    def write_retval(self, src_reg: Reg) -> None:
        if isinstance(src_reg, RealReg):
            name = str(src_reg).lstrip('%')
            if name in REG_NAMES_TO_LETTERS:
                self.retval = name
                return
        self.retval = ''
        self.emit(f'movq {src_reg}, ![ret]')

    def emit_prologue(self) -> None:
        print('    asm volatile (')

    def emit(self, line: str) -> None:
        line = line.replace('%', '%%')
        line = line.replace('!', '%')
        print(f'    "{line}\\n"')

    def emit_epilogue(self) -> None:
        clobbers = self.reg_store.clobbers()
        outputs = []
        inputs = []

        def _add_output(keyword, reg_name, is_read, force_earlyclobber):
            earlyclobber = force_earlyclobber
            mode = '+' if is_read else '='
            if reg_name == '':
                letter = 'r'
            else:
                letter = REG_NAMES_TO_LETTERS[reg_name]
                if reg_name in clobbers:
                    clobbers.remove(reg_name)
                    earlyclobber = True
            if earlyclobber:
                mode += '&'
            outputs.append(f'[{keyword}] "{mode}{letter}" ({keyword})')

        def _add_input(keyword, reg_name):
            letter = 'r' if reg_name == '' else REG_NAMES_TO_LETTERS[reg_name]
            inputs.append(f'[{keyword}] "{letter}" ({keyword})')

        for i, (is_written_to, reg_name) in enumerate(self.args):
            is_same_reg_as_retval = (reg_name != '' and reg_name == self.retval)
            if is_written_to and not is_same_reg_as_retval:
                _add_output(f'arg{i}', reg_name, is_read=True, force_earlyclobber=False)
            else:
                _add_input(f'arg{i}', reg_name)

        if self.retval is not None:
            _add_output('ret', self.retval, is_read=False, force_earlyclobber=self.retval_earlyclobber)

        if self.needs_zero_input:
            inputs.append('[zero] "r" ((uint64_t) 0)')

        clobbers.append('cc')
        clobbers.append('memory')
        clobbers.sort()
        clobbers = [f'"{s}"' for s in clobbers]

        print(f'    : {", ".join(outputs) or "/*no outputs*/"}')
        print(f'    : {", ".join(inputs) or "/*no inputs*/"}')
        print(f'    : {", ".join(clobbers) or "/*no clobbers*/"}')
        print('    );')

    def gen_label(self) -> str:
        self.label_counter += 1
        return f'.L!=_{self.label_counter}'

    def label_here(self, label: str) -> None:
        self.emit(f'{label}:')


#------------------------------------------------------------------------------


# Multiply 'src[0]...src[n]' by 'mulby', writing/adding result to 'dst[0]...dst[n]'.
#
# If 'i >= undef_from', then the value of 'dst[i]' is assumed to be "undefined" (but implicitly
# contain the value of zero), otherwise 'dst[i]' is added to.
#
# 'zero' must be either a register with value of zero, or constant "$0".
#
# If 'drop_last_carry' is False (default), returns register with last carry; you must "untake" it.
# Otherwise, returns None.
def mul_aux(
        emitter: Emitter,
        n: int,
        undef_from: int,
        src: AnyPointerReg,
        mulby: Union[Reg, AnyPointerReg],
        dst: AnyPointerReg,
        zero: Union[Reg, str],
        drop_last_carry: bool=False) -> Optional[Reg]:

    rax = emitter.reg_store.take_by_name('rax', write=True)
    rdx = emitter.reg_store.take_by_name('rdx', write=True)

    reg_carry = emitter.reg_store.take(write=True)

    for i in range(n):
        drop_following_carry = drop_last_carry and (i + 1 == n)

        if i:
            emitter.emit(f'movq {rdx}, {reg_carry}')

        emitter.emit(f'movq {mulby}, {rax}')

        if drop_following_carry:
            emitter.emit(f'imulq {src.displace(i)}, {rax}')
        else:
            emitter.emit(f'mulq {src.displace(i)}')

        if i:
            emitter.emit(f'addq {reg_carry}, {rax}')
            if not drop_following_carry:
                emitter.emit(f'adcq {zero}, {rdx}')

        if i >= undef_from:
            emitter.emit(f'movq {rax}, {dst.displace(i)}')
        else:
            emitter.emit(f'addq {rax}, {dst.displace(i)}')
            if not drop_following_carry:
                emitter.emit(f'adcq {zero}, {rdx}')

    emitter.reg_store.untake(reg_carry)
    emitter.reg_store.untake(rax)
    if drop_last_carry:
        emitter.reg_store.untake(rdx)
        return None
    else:
        return rdx


def mul_aux_bmi2(
        emitter: Emitter,
        n: int,
        undef_from: int,
        src: AnyPointerReg,
        rdx: Reg,
        dst: AnyPointerReg,
        zero: Union[Reg, str],
        drop_last_carry: bool=False,
        reg_carry: Optional[Reg]=None) -> Tuple[Optional[Reg], bool]:

    if reg_carry is None:
        reg_carry = emitter.reg_store.take(write=True)
    reg_lo = emitter.reg_store.take(write=True)
    reg_hi = emitter.reg_store.take(write=True)

    if n % 2 == 1:
        reg_hi, reg_carry = reg_carry, reg_hi

    cy_meaningful = False

    for i in range(n):
        drop_following_carry = drop_last_carry and (i + 1 == n)

        emitter.emit(f'mulxq {src.displace(i)}, {reg_lo}, {reg_hi}')

        if i:
            insn = 'adcq' if cy_meaningful else 'addq'
            emitter.emit(f'{insn} {reg_carry}, {reg_lo}')
            cy_meaningful = True

        if i >= undef_from:
            emitter.emit(f'movq {reg_lo}, {dst.displace(i)}')
        else:
            if cy_meaningful and not drop_following_carry:
                emitter.emit(f'adcq {zero}, {reg_hi}')
            emitter.emit(f'addq {reg_lo}, {dst.displace(i)}')
            cy_meaningful = True

        reg_hi, reg_carry = reg_carry, reg_hi

    emitter.reg_store.untake(reg_lo)
    emitter.reg_store.untake(reg_hi)
    if drop_last_carry:
        emitter.reg_store.untake(reg_carry)
        reg_carry = None
    return reg_carry, cy_meaningful


def mul_aux_auto(
        emitter: Emitter,
        n: int,
        undef_from: int,
        src: AnyPointerReg,
        b: AnyPointerReg,
        dst: AnyPointerReg,
        zero: Union[Reg, str],
        drop_last_carry: bool=False) -> Optional[Reg]:

    if n == 1:
        return mul_aux(emitter, n, undef_from, src, b, dst, zero, drop_last_carry)
    else:
        reg_mulby = emitter.reg_store.take(write=True)
        emitter.emit(f'movq {b}, {reg_mulby}')
        result = mul_aux(emitter, n, undef_from, src, reg_mulby, dst, zero, drop_last_carry)
        emitter.reg_store.untake(reg_mulby)
        return result


# Multiply 'src[0]...src[n]' by 'b[0]', writing/adding result to 'dst[0]...dst[n+1]'.
#
# If 'i >= undef_from', then the value of 'dst[i]' is assumed to be "undefined" (but implicitly
# contain the value of zero), otherwise 'dst[i]' is added to.
def long_mul_step(
        emitter: Emitter,
        n: int,
        undef_from: int,
        src: AnyPointerReg,
        b: AnyPointerReg,
        dst: AnyPointerReg,
        zero: Union[Reg, str]) -> None:

    reg_last_carry = mul_aux_auto(
        emitter,
        n, undef_from,
        src, b, dst,
        zero)

    if n >= undef_from:
        emitter.emit(f'movq {reg_last_carry}, {dst.displace(n)}')
    else:
        emitter.emit(f'addq {reg_last_carry}, {dst.displace(n)}')

    emitter.reg_store.untake(reg_last_carry)


def long_mul_step_bmi2(
        emitter: Emitter,
        n: int,
        undef_from: int,
        src: AnyPointerReg,
        rdx: Reg,
        dst: AnyPointerReg,
        zero: Union[Reg, str]) -> None:

    reg_last_carry, cy_meaningful = mul_aux_bmi2(
        emitter,
        n, undef_from,
        src, rdx, dst,
        zero)

    if n >= undef_from:
        if cy_meaningful:
            emitter.emit(f'adcq {zero}, {reg_last_carry}')
        emitter.emit(f'movq {reg_last_carry}, {dst.displace(n)}')
    else:
        insn = 'adcq' if cy_meaningful else 'addq'
        emitter.emit(f'{insn} {reg_last_carry}, {dst.displace(n)}')

    emitter.reg_store.untake(reg_last_carry)


#------------------------------------------------------------------------------


def FUNC_mul(emitter, n, m):
    if n < m:
        raise ValueError('expected n >= m')

    emitter.add_fixed_reg('rax')
    emitter.add_fixed_reg('rdx')

    reg_a = emitter.take_arg_reg(index=0, write=False)
    reg_b = emitter.take_arg_reg(index=1, write=False)
    reg_dst = emitter.take_arg_reg(index=2, write=False)

    a = PointerReg(reg_a)
    b = PointerReg(reg_b)
    dst = PointerReg(reg_dst)

    zero = '$0'

    for i in range(m):
        if i:
            undef_from = n
        else:
            undef_from = 0

        long_mul_step(
            emitter,
            n, undef_from,
            a, b.displace(i), dst.displace(i),
            zero=zero)


def FUNC_mul_bmi2(emitter, n, m):
    if n < m:
        raise ValueError('expected n >= m')

    emitter.add_fixed_reg('rdx')

    reg_a = emitter.take_arg_reg(index=0, write=False)
    reg_b = emitter.take_arg_reg(index=1, write=False)
    reg_dst = emitter.take_arg_reg(index=2, write=False)

    a = PointerReg(reg_a)
    b = PointerReg(reg_b)
    dst = PointerReg(reg_dst)

    rdx = emitter.reg_store.take_by_name('rdx', write=True)

    zero = '$0'

    for i in range(m):
        if i:
            undef_from = n
        else:
            undef_from = 0

        emitter.emit(f'movq {b.displace(i)}, {rdx}')

        long_mul_step_bmi2(
            emitter,
            n, undef_from,
            a, rdx, dst.displace(i),
            zero=zero)


def FUNC_mul_lo(emitter, n):
    emitter.add_fixed_reg('rax')
    emitter.add_fixed_reg('rdx')

    reg_a = emitter.take_arg_reg(index=0, write=False)
    reg_b = emitter.take_arg_reg(index=1, write=False)
    reg_dst = emitter.take_arg_reg(index=2, write=False)

    a = PointerReg(reg_a)
    b = PointerReg(reg_b)
    dst = PointerReg(reg_dst)

    zero = '$0'

    for i in range(n):
        if i:
            undef_from = n
        else:
            undef_from = 0

        mul_aux_auto(
            emitter,
            n - i, undef_from,
            a, b.displace(i), dst.displace(i),
            zero=zero,
            drop_last_carry=True)


def FUNC_mul_lo_bmi2(emitter, n):
    emitter.add_fixed_reg('rdx')

    reg_a = emitter.take_arg_reg(index=0, write=False)
    reg_b = emitter.take_arg_reg(index=1, write=False)
    reg_dst = emitter.take_arg_reg(index=2, write=False)

    a = PointerReg(reg_a)
    b = PointerReg(reg_b)
    dst = PointerReg(reg_dst)

    rdx = emitter.reg_store.take_by_name('rdx', write=True)

    for i in range(n):
        if i:
            undef_from = n
        else:
            undef_from = 0

        emitter.emit(f'movq {b.displace(i)}, {rdx}')

        mul_aux_bmi2(
            emitter,
            n - i, undef_from,
            a, rdx, dst.displace(i),
            zero='$0',
            drop_last_carry=True)


def FUNC_mul_q(emitter, n):
    emitter.add_fixed_reg('rax')
    emitter.add_fixed_reg('rdx')

    reg_src = emitter.take_arg_reg(index=0, write=False)
    reg_m = emitter.take_arg_reg(index=1, write=False)
    reg_dst = emitter.take_arg_reg(index=2, write=False)

    zero = '$0'

    src = PointerReg(reg_src)
    dst = PointerReg(reg_dst)

    reg_last_carry = mul_aux(
        emitter,
        n, 0,
        src, reg_m, dst,
        zero=zero)

    emitter.write_retval(reg_last_carry)


def FUNC_mul_q_bmi2(emitter, n):
    emitter.add_fixed_reg('rdx')
    emitter.set_nargs(3)

    reg_src = emitter.take_arg_reg(index=0, write=False)
    reg_m = emitter.take_arg_reg(index=1, into_reg_name='rdx', write=False)
    reg_dst = emitter.take_arg_reg(index=2, write=False)

    reg_result = emitter.take_retval_reg(may_overwrite_taken=False)

    src = PointerReg(reg_src)
    dst = PointerReg(reg_dst)

    reg_last_carry, cy_meaningful = mul_aux_bmi2(
        emitter,
        n, 0,
        src, reg_m, dst,
        zero='$0',
        reg_carry=reg_result)

    assert str(reg_last_carry) == str(reg_result)

    if cy_meaningful:
        emitter.emit(f'adcq $0, {reg_last_carry}')


def FUNC_div_q(emitter, n, operation='div'):
    emitter.add_fixed_reg('rax')
    emitter.add_fixed_reg('rdx')

    reg_a = emitter.take_arg_reg(index=0, write=False)
    reg_m = emitter.take_arg_reg(index=1, write=False)
    if operation == 'div':
        reg_dst = emitter.take_arg_reg(index=2, write=False)
        dst = PointerReg(reg_dst)
    elif operation == 'mod':
        reg_dst = None
        dst = None
    else:
        raise ValueError('expected either "div" or "mod" as operation')

    a = PointerReg(reg_a)

    rax = emitter.reg_store.take_by_name('rax', write=True)
    rdx = emitter.reg_store.take_by_name('rdx', write=True)

    emitter.emit(f'xorl {rdx.e_part()}, {rdx.e_part()}')

    for i in reversed(range(n)):
        emitter.emit(f'movq {a.displace(i)}, {rax}')
        emitter.emit(f'divq {reg_m}')
        if dst is not None:
            emitter.emit(f'movq {rax}, {dst.displace(i)}')

    emitter.write_retval(rdx)


def do_shr(emitter, src, reg_dst, reg_donor, reg_count, reg_neg_count, reg_scratch, is_signed, use_bmi2):
    base_insn = 'sar' if (is_signed and reg_donor is None) else 'shr'
    if use_bmi2:
        emitter.emit(f'{base_insn}xq {reg_count}, {src}, {reg_dst}')
        if reg_donor is not None:
            emitter.emit(f'shlxq {reg_neg_count}, {reg_donor}, {reg_scratch}')
            emitter.emit(f'orq {reg_scratch}, {reg_dst}')
    else:
        if str(src) != str(reg_dst):
            emitter.emit(f'movq {src}, {reg_dst}')
        if reg_donor is not None:
            emitter.emit(f'shrdq %cl, {reg_donor}, {reg_dst}')
        else:
            emitter.emit(f'{base_insn}q %cl, {reg_dst}')


def do_shl(emitter, src, reg_dst, reg_donor, reg_count, reg_neg_count, reg_scratch, use_bmi2):
    if use_bmi2:
        emitter.emit(f'shlxq {reg_count}, {src}, {reg_dst}')
        if reg_donor is not None:
            emitter.emit(f'shrxq {reg_neg_count}, {reg_donor}, {reg_scratch}')
            emitter.emit(f'orq {reg_scratch}, {reg_dst}')
    else:
        if str(src) != str(reg_dst):
            emitter.emit(f'movq {src}, {reg_dst}')
        if reg_donor is not None:
            emitter.emit(f'shldq %cl, {reg_donor}, {reg_dst}')
        else:
            emitter.emit(f'shlq %cl, {reg_dst}')


def FUNC_shr(emitter, n, is_signed=False, use_bmi2=False):
    if not use_bmi2:
        emitter.add_fixed_reg('rcx')

    reg_a = emitter.take_arg_reg(index=0, write=False)
    if use_bmi2:
        reg_count = emitter.take_arg_reg(index=1, write=False)
    else:
        reg_count = emitter.take_arg_reg(index=1, write=False, into_reg_name='rcx')
    reg_dst = emitter.take_arg_reg(index=2, write=False)

    reg_tmp_1 = emitter.reg_store.take(write=True)
    reg_tmp_2 = emitter.reg_store.take(write=True)

    if use_bmi2:
        reg_neg_count = emitter.reg_store.take(write=True)
        reg_scratch = emitter.reg_store.take(write=True)
        emitter.emit(f'movq {reg_count}, {reg_neg_count}')
        emitter.emit(f'negq {reg_neg_count}')
    else:
        label_done = None
        reg_neg_count = None
        reg_scratch = None

    a = PointerReg(reg_a)
    dst = PointerReg(reg_dst)

    for i in range(n):
        if i == 0:
            cur_src = a.displace(i)
        else:
            cur_src = reg_tmp_1

        if i == n - 1:
            cur_donor = None
        else:
            emitter.emit(f'movq {a.displace(i + 1)}, {reg_tmp_2}')
            cur_donor = reg_tmp_2

        do_shr(
            emitter,
            src=cur_src,
            reg_dst=reg_tmp_1,
            reg_donor=cur_donor,
            reg_count=reg_count,
            reg_neg_count=reg_neg_count,
            reg_scratch=reg_scratch,
            is_signed=is_signed,
            use_bmi2=use_bmi2)

        emitter.emit(f'movq {reg_tmp_1}, {dst.displace(i)}')
        reg_tmp_1, reg_tmp_2 = reg_tmp_2, reg_tmp_1


def FUNC_shl(emitter, n, use_bmi2=False):
    if not use_bmi2:
        emitter.add_fixed_reg('rcx')

    reg_a = emitter.take_arg_reg(index=0, write=False)
    if use_bmi2:
        reg_count = emitter.take_arg_reg(index=1, write=False)
    else:
        reg_count = emitter.take_arg_reg(index=1, write=False, into_reg_name='rcx')
    reg_dst = emitter.take_arg_reg(index=2, write=False)

    reg_tmp_1 = emitter.reg_store.take(write=True)
    reg_tmp_2 = emitter.reg_store.take(write=True)

    if use_bmi2:
        reg_neg_count = emitter.reg_store.take(write=True)
        reg_scratch = emitter.reg_store.take(write=True)
        emitter.emit(f'movq {reg_count}, {reg_neg_count}')
        emitter.emit(f'negq {reg_neg_count}')
    else:
        reg_neg_count = None
        reg_scratch = None

    a = PointerReg(reg_a)
    dst = PointerReg(reg_dst)

    for i in reversed(range(n)):
        if i == n - 1:
            cur_src = a.displace(i)
        else:
            cur_src = reg_tmp_1

        if i == 0:
            cur_donor = None
        else:
            emitter.emit(f'movq {a.displace(i - 1)}, {reg_tmp_2}')
            cur_donor = reg_tmp_2

        do_shl(
            emitter,
            src=cur_src,
            reg_dst=reg_tmp_1,
            reg_donor=cur_donor,
            reg_count=reg_count,
            reg_neg_count=reg_neg_count,
            reg_scratch=reg_scratch,
            use_bmi2=use_bmi2)

        emitter.emit(f'movq {reg_tmp_1}, {dst.displace(i)}')
        reg_tmp_1, reg_tmp_2 = reg_tmp_2, reg_tmp_1


class AORS_ADD:
    ADDSUB = 'add'
    ADCSBB = 'adc'


class AORS_SUB:
    ADDSUB = 'sub'
    ADCSBB = 'sbb'


def FUNC_aors(emitter, n, aors):
    reg_a = emitter.take_arg_reg(index=0, write=False)
    reg_b = emitter.take_arg_reg(index=1, write=False)

    reg_tmp = emitter.reg_store.take(write=True)

    a = PointerReg(reg_a)
    b = PointerReg(reg_b)

    for i in range(n):
        emitter.emit(f'movq {b.displace(i)}, {reg_tmp}')
        if i:
            emitter.emit(f'{aors.ADCSBB}q {reg_tmp}, {a.displace(i)}')
        else:
            emitter.emit(f'{aors.ADDSUB}q {reg_tmp}, {a.displace(i)}')

    ret = emitter.take_retval_reg()
    emitter.emit(f'sbbq {ret}, {ret}')


def aors_masked_aux(emitter, a, b, reg_c, reg_mask, m_regs, aors, save=False, restore=False):
    for i in range(len(m_regs)):
        emitter.emit(f'movq {b.displace(i)}, {m_regs[i]}')
        emitter.emit(f'andq {reg_mask}, {m_regs[i]}')

    if restore:
        assert reg_c is not None
        emitter.emit(f'shlq $1, {reg_c}')

    for i in range(len(m_regs)):
        if (not restore) and (i == 0):
            emitter.emit(f'{aors.ADDSUB}q {m_regs[i]}, {a.displace(i)}')
        else:
            emitter.emit(f'{aors.ADCSBB}q {m_regs[i]}, {a.displace(i)}')

    if save:
        assert reg_c is not None
        emitter.emit(f'sbbq {reg_c}, {reg_c}')


def FUNC_aors_masked(emitter, n, aors, m):
    reg_a = emitter.take_arg_reg(index=0, write=False)
    reg_b = emitter.take_arg_reg(index=1, write=False)
    reg_mask = emitter.take_arg_reg(index=2, write=False)

    a = PointerReg(reg_a)
    b = PointerReg(reg_b)

    if n > m:
        reg_c = emitter.reg_store.take(write=True)
        m_regs = [emitter.reg_store.take(write=True) for _ in range(m)]
        restore = False
        while n:
            this_m = min(n, m)
            aors_masked_aux(
                emitter,
                a=a,
                b=b,
                reg_c=reg_c,
                reg_mask=reg_mask,
                m_regs=m_regs[:this_m],
                aors=aors,
                save=this_m != n,
                restore=restore)
            a = a.displace(this_m)
            b = b.displace(this_m)
            restore = True
            n -= this_m
    else:
        m_regs = [emitter.reg_store.take(write=True) for _ in range(n)]
        aors_masked_aux(
            emitter,
            a=a,
            b=b,
            reg_c=None,
            reg_mask=reg_mask,
            m_regs=m_regs,
            aors=aors,
            save=False,
            restore=False)

    ret = emitter.take_retval_reg()
    emitter.emit(f'sbbq {ret}, {ret}')


def FUNC_aors_q(emitter, n, aors, leaky=False):
    reg_a = emitter.take_arg_reg(index=0, write=False)
    reg_b = emitter.take_arg_reg(index=1, write=False)

    zero = '$0'

    a = PointerReg(reg_a)

    if leaky and n > 2:
        label_done = emitter.gen_label()
    else:
        label_done = None

    for i in range(n):
        if i:
            emitter.emit(f'{aors.ADCSBB}q {zero}, {a.displace(i)}')
            if (label_done is not None) and (i != n - 1):
                emitter.emit(f'jnc {label_done}')
        else:
            emitter.emit(f'{aors.ADDSUB}q {reg_b}, {a.displace(i)}')
            # We don't want to emit a 'jnc {label_done}' here since the probability of having a
            # carry is very close to 1/2 (can't be predicted well by the branch predictor).
            # For the following words it is about 2 ** -64.

    if label_done is not None:
        emitter.label_here(label_done)

    ret = emitter.take_retval_reg()
    emitter.emit(f'sbbq {ret}, {ret}')


def FUNC_negate(emitter, n):
    reg_a = emitter.take_arg_reg(index=0, write=False)
    reg_b = emitter.take_arg_reg(index=1, write=False)

    reg_tmp = emitter.reg_store.take(write=True)

    a = PointerReg(reg_a)
    b = PointerReg(reg_b)

    for i in range(n):
        if i:
            emitter.emit(f'movq $0, {reg_tmp}')
            emitter.emit(f'sbbq {a.displace(i)}, {reg_tmp}')
        else:
            emitter.emit(f'movq {a.displace(i)}, {reg_tmp}')
            emitter.emit(f'negq {reg_tmp}')
        emitter.emit(f'movq {reg_tmp}, {b.displace(i)}')

    ret = emitter.take_retval_reg()
    emitter.emit(f'sbbq {ret}, {ret}')


def FUNC_cmplt(emitter, n, is_signed=False):
    reg_a = emitter.take_arg_reg(index=0, write=False)
    reg_b = emitter.take_arg_reg(index=1, write=False)

    reg_tmp = emitter.reg_store.take(write=True)

    a = PointerReg(reg_a)
    b = PointerReg(reg_b)

    for i in range(n):
        emitter.emit(f'movq {a.displace(i)}, {reg_tmp}')
        if i:
            emitter.emit(f'sbbq {b.displace(i)}, {reg_tmp}')
        else:
            emitter.emit(f'subq {b.displace(i)}, {reg_tmp}')

    ret = emitter.take_retval_reg()
    if is_signed:
        emitter.emit(f'setl {ret.l_part()}')
        emitter.emit(f'movzbq {ret.l_part()}, {ret}')
    else:
        emitter.emit(f'sbbq {ret}, {ret}')


def FUNC_cmple(emitter, n, is_signed=False):
    reg_a = emitter.take_arg_reg(index=0, write=False)
    reg_b = emitter.take_arg_reg(index=1, write=False)

    reg_tmp = emitter.reg_store.take(write=True)

    a = PointerReg(reg_a)
    b = PointerReg(reg_b)

    for i in range(n):
        emitter.emit(f'movq {b.displace(i)}, {reg_tmp}')
        if i:
            emitter.emit(f'sbbq {a.displace(i)}, {reg_tmp}')
        else:
            emitter.emit(f'subq {a.displace(i)}, {reg_tmp}')

    ret = emitter.take_retval_reg()
    if is_signed:
        emitter.emit(f'setge {ret.l_part()}')
        emitter.emit(f'movzbq {ret.l_part()}, {ret}')
    else:
        emitter.emit(f'sbbq {ret}, {ret}')
        emitter.emit(f'notq {ret}')


def FUNC_cmpeq(emitter, n):
    reg_a = emitter.take_arg_reg(index=0, write=False)
    reg_b = emitter.take_arg_reg(index=1, write=False)

    reg_tmp = emitter.reg_store.take(write=True)

    ret = emitter.take_retval_reg(may_overwrite_taken=False)

    a = PointerReg(reg_a)
    b = PointerReg(reg_b)

    for i in range(n):
        if i:
            emitter.emit(f'movq {a.displace(i)}, {reg_tmp}')
            emitter.emit(f'xorq {b.displace(i)}, {reg_tmp}')
            emitter.emit(f'orq {reg_tmp}, {ret}')
        else:
            emitter.emit(f'movq {a.displace(i)}, {ret}')
            emitter.emit(f'xorq {b.displace(i)}, {ret}')

    emitter.emit(f'subq $1, {ret}')
    emitter.emit(f'sbbq {ret}, {ret}')


def cond_shr_words(n, amount, assign_callback, cond):
    for i in range(n):
        src_i = i + amount
        if src_i >= n:
            assign_callback(src_i=None, dst_i=i, cond=cond)
        else:
            assign_callback(src_i=src_i, dst_i=i, cond=cond)


def cond_shl_words(n, amount, assign_callback, cond):
    for i in reversed(range(n)):
        src_i = i - amount
        if src_i < 0:
            assign_callback(src_i=None, dst_i=i, cond=cond)
        else:
            assign_callback(src_i=src_i, dst_i=i, cond=cond)


def dumb_shr_words(emitter, reg_b, n, assign_callback):
    for i in range(n):
        if i:
            emitter.emit(f'cmpq ${i}, {reg_b}')
        else:
            emitter.emit(f'testq {reg_b}, {reg_b}')
        cond_shr_words(n=n - i, amount=1, assign_callback=assign_callback, cond='a')


def dumb_shl_words(emitter, reg_b, n, assign_callback):
    for i in range(n):
        if i:
            emitter.emit(f'cmpq ${i}, {reg_b}')
        else:
            emitter.emit(f'testq {reg_b}, {reg_b}')

        def new_assign_callback(src_i, dst_i, cond):
            dst_i += i
            if src_i is not None:
                src_i += i
            assign_callback(src_i=src_i, dst_i=dst_i, cond=cond)

        cond_shl_words(n=n - i, amount=1, assign_callback=new_assign_callback, cond='a')


def fancy_shift_words(emitter, reg_b, n, cond_shx_words, assign_callback):
    def perform_pass(cond, amount):
        cond_shx_words(n=n, amount=amount, assign_callback=assign_callback, cond=cond)

    i = 0
    while True:
        bit = 1 << i
        if bit >= n:
            break
        emitter.emit(f'testq ${bit}, {reg_b}')
        perform_pass(cond='nz', amount=bit)
        i += 1

    emitter.emit(f'cmpq ${n - 1}, {reg_b}')
    perform_pass(cond='a', amount=n)


def shift_words_auto(emitter, reg_b, n, direction, assign_callback):
    if direction == 'left':
        left = True
    elif direction == 'right':
        left = False
    else:
        raise ValueError('expected either "left" or "right" as direction')

    if n <= 8:
        if left:
            dumb_shl_words(emitter, reg_b, n, assign_callback)
        else:
            dumb_shr_words(emitter, reg_b, n, assign_callback)
    else:
        if left:
            fancy_shift_words(emitter, reg_b, n, cond_shl_words, assign_callback)
        else:
            fancy_shift_words(emitter, reg_b, n, cond_shr_words, assign_callback)


def FUNC_shift_words(emitter, n, direction, is_signed, m=8):
    reg_a = emitter.take_arg_reg(index=0, write=False)
    reg_b = emitter.take_arg_reg(index=1, write=False)
    reg_c = emitter.take_arg_reg(index=2, write=False)

    a = PointerReg(reg_a)
    c = PointerReg(reg_c)

    if n > m:
        if is_signed:
            reg_fill = emitter.reg_store.take(write=True)
            emitter.emit(f'movq {a.displace(n - 1)}, {reg_fill}')
            emitter.emit(f'sarq $63, {reg_fill}')
        else:
            reg_fill = emitter.take_zero_reg()

        reg_tmp = emitter.reg_store.take(write=True)

        written_to_c_at = [False for _ in range(n)]

        def get_ptr(i):
            if written_to_c_at[i]:
                return c.displace(i)
            else:
                return a.displace(i)

        def assign_callback(src_i, dst_i, cond):
            emitter.emit(f'movq {get_ptr(dst_i)}, {reg_tmp}')
            if src_i is None:
                emitter.emit(f'cmov{cond}q {reg_fill}, {reg_tmp}')
            else:
                emitter.emit(f'cmov{cond}q {get_ptr(src_i)}, {reg_tmp}')
            emitter.emit(f'movq {reg_tmp}, {c.displace(dst_i)}')

            written_to_c_at[dst_i] = True

        shift_words_auto(emitter, reg_b, n, direction, assign_callback)

        assert all(written_to_c_at)

    else:
        tmp_regs = [emitter.reg_store.take(write=True) for _ in range(n)]
        for i in range(n):
            emitter.emit(f'movq {a.displace(i)}, {tmp_regs[i]}')

        if is_signed:
            reg_fill = emitter.reg_store.take(write=True)
            emitter.emit(f'movq {tmp_regs[-1]}, {reg_fill}')
            emitter.emit(f'sarq $63, {reg_fill}')
        else:
            reg_fill = emitter.take_zero_reg()

        def assign_callback(src_i, dst_i, cond):
            if src_i is None:
                emitter.emit(f'cmov{cond}q {reg_fill}, {tmp_regs[dst_i]}')
            else:
                emitter.emit(f'cmov{cond}q {tmp_regs[src_i]}, {tmp_regs[dst_i]}')

        shift_words_auto(emitter, reg_b, n, direction, assign_callback)

        for i in range(n):
            emitter.emit(f'movq {tmp_regs[i]}, {c.displace(i)}')


#------------------------------------------------------------------------------


cached_caps = {}


def run_process(args):
    p = subprocess.run(args)
    if p.returncode == 0:
        return True
    else:
        print(f'Command {args} failed with code {p.returncode}', file=sys.stderr)
        return False


def check_cap(cap_name):
    override = os.getenv(f'FIWIA_CAP_{cap_name.upper()}')
    if override:
        return bool(int(override))
    my_dir = os.path.dirname(os.path.abspath(__file__))
    if not run_process([os.getenv('CC') or 'gcc', f'{my_dir}/check_cap.c', '-o', f'{my_dir}/check_cap']):
        raise ValueError('cannot compile check_cap')
    return run_process([f'{my_dir}/check_cap', cap_name])


def check_cap_cached(cap_name):
    if cap_name in cached_caps:
        return cached_caps[cap_name]
    result = check_cap(cap_name)
    cached_caps[cap_name] = result
    return result


def choose_plain_or_bmi2(func_plain, func_bmi2, *args, **kwargs):
    if check_cap_cached('bmi2'):
        return func_bmi2(*args, **kwargs)
    else:
        return func_plain(*args, **kwargs)


class GeneratedFunc:
    def __init__(self, name, proto, callback):
        # C function name
        self.name = name

        # A string of the following form: "param_list -> return_value", where:
        #  - 'param_list' is comma-delimited,
        #  - '#' means "limb" (uint64_t),
        #  - '#*' means pointer to limb,
        #  - '@#*' means constant pointer to limb,
        #  - 'void' means void.
        self.proto = proto

        self.callback = callback


PREFIX = 'asm'


def get_generated_funcs(n, is_inline_asm):
    aors_masked_m = 8 if is_inline_asm else 4
    shift_words_m = 8 if is_inline_asm else 4
    return [
        GeneratedFunc(
            name=f'{PREFIX}_add_{n}',
            proto='#*, @#* -> #',
            callback=lambda emitter: FUNC_aors(emitter, n, AORS_ADD)),
        GeneratedFunc(
            name=f'{PREFIX}_sub_{n}',
            proto='#*, @#* -> #',
            callback=lambda emitter: FUNC_aors(emitter, n, AORS_SUB)),
        GeneratedFunc(
            name=f'{PREFIX}_add_masked_{n}',
            proto='#*, @#*, # -> #',
            callback=lambda emitter: FUNC_aors_masked(emitter, n, AORS_ADD, m=aors_masked_m)),
        GeneratedFunc(
            name=f'{PREFIX}_sub_masked_{n}',
            proto='#*, @#*, # -> #',
            callback=lambda emitter: FUNC_aors_masked(emitter, n, AORS_SUB, m=aors_masked_m)),
        GeneratedFunc(
            name=f'{PREFIX}_negate_{n}',
            proto='@#*, #* -> #',
            callback=lambda emitter: FUNC_negate(emitter, n)),

        GeneratedFunc(
            name=f'{PREFIX}_add_q_{n}',
            proto='#*, # -> #',
            callback=lambda emitter: FUNC_aors_q(emitter, n, AORS_ADD)),
        GeneratedFunc(
            name=f'{PREFIX}_sub_q_{n}',
            proto='#*, # -> #',
            callback=lambda emitter: FUNC_aors_q(emitter, n, AORS_SUB)),

        GeneratedFunc(
            name=f'{PREFIX}_add_q_leaky_{n}',
            proto='#*, # -> #',
            callback=lambda emitter: FUNC_aors_q(emitter, n, AORS_ADD, leaky=True)),
        GeneratedFunc(
            name=f'{PREFIX}_sub_q_leaky_{n}',
            proto='#*, # -> #',
            callback=lambda emitter: FUNC_aors_q(emitter, n, AORS_SUB, leaky=True)),

        GeneratedFunc(
            name=f'{PREFIX}_cmplt_{n}',
            proto='@#*, @#* -> #',
            callback=lambda emitter: FUNC_cmplt(emitter, n)),
        GeneratedFunc(
            name=f'{PREFIX}_cmple_{n}',
            proto='@#*, @#* -> #',
            callback=lambda emitter: FUNC_cmple(emitter, n)),
        GeneratedFunc(
            name=f'{PREFIX}_S_cmplt_{n}',
            proto='@#*, @#* -> #',
            callback=lambda emitter: FUNC_cmplt(emitter, n, is_signed=True)),
        GeneratedFunc(
            name=f'{PREFIX}_S_cmple_{n}',
            proto='@#*, @#* -> #',
            callback=lambda emitter: FUNC_cmple(emitter, n, is_signed=True)),
        GeneratedFunc(
            name=f'{PREFIX}_cmpeq_{n}',
            proto='@#*, @#* -> #',
            callback=lambda emitter: FUNC_cmpeq(emitter, n)),
        GeneratedFunc(
            name=f'{PREFIX}_mul_q_{n}',
            proto='@#*, #, #* -> #',
            callback=lambda emitter: choose_plain_or_bmi2(FUNC_mul_q, FUNC_mul_q_bmi2, emitter, n)),
        GeneratedFunc(
            name=f'{PREFIX}_div_q_{n}',
            proto='@#*, #, #* -> #',
            callback=lambda emitter: FUNC_div_q(emitter, n)),
        GeneratedFunc(
            name=f'{PREFIX}_mod_q_{n}',
            proto='@#*, # -> #',
            callback=lambda emitter: FUNC_div_q(emitter, n, operation='mod')),
        GeneratedFunc(
            name=f'{PREFIX}_mul_lo_{n}',
            proto='@#*, @#*, #* -> void',
            callback=lambda emitter: choose_plain_or_bmi2(FUNC_mul_lo, FUNC_mul_lo_bmi2, emitter, n)),
        GeneratedFunc(
            name=f'{PREFIX}_mul_{n}',
            proto='@#*, @#*, #* -> void',
            callback=lambda emitter: choose_plain_or_bmi2(FUNC_mul, FUNC_mul_bmi2, emitter, n, n)),

        GeneratedFunc(
            name=f'{PREFIX}_shr_nz_{n}',
            proto='@#*, #, #* -> void',
            callback=lambda emitter: FUNC_shr(emitter, n, use_bmi2=check_cap_cached('bmi2'))),
        GeneratedFunc(
            name=f'{PREFIX}_S_shr_nz_{n}',
            proto='@#*, #, #* -> void',
            callback=lambda emitter: FUNC_shr(emitter, n, is_signed=True, use_bmi2=check_cap_cached('bmi2'))),
        GeneratedFunc(
            name=f'{PREFIX}_shl_nz_{n}',
            proto='@#*, #, #* -> void',
            callback=lambda emitter: FUNC_shl(emitter, n, use_bmi2=check_cap_cached('bmi2'))),

        GeneratedFunc(
            name=f'{PREFIX}_shr_{n}',
            proto='@#*, #, #* -> void',
            callback=lambda emitter: FUNC_shr(emitter, n, use_bmi2=False)),
        GeneratedFunc(
            name=f'{PREFIX}_S_shr_{n}',
            proto='@#*, #, #* -> void',
            callback=lambda emitter: FUNC_shr(emitter, n, is_signed=True, use_bmi2=False)),
        GeneratedFunc(
            name=f'{PREFIX}_shl_{n}',
            proto='@#*, #, #* -> void',
            callback=lambda emitter: FUNC_shl(emitter, n, use_bmi2=False)),

        GeneratedFunc(
            name=f'{PREFIX}_shr_words_{n}',
            proto='@#*, #, #* -> void',
            callback=lambda emitter: FUNC_shift_words(
                emitter, n, direction='right', is_signed=False, m=shift_words_m)),
        GeneratedFunc(
            name=f'{PREFIX}_S_shr_words_{n}',
            proto='@#*, #, #* -> void',
            callback=lambda emitter: FUNC_shift_words(
                emitter, n, direction='right', is_signed=True, m=shift_words_m)),
        GeneratedFunc(
            name=f'{PREFIX}_shl_words_{n}',
            proto='@#*, #, #* -> void',
            callback=lambda emitter: FUNC_shift_words(
                emitter, n, direction='left', is_signed=False, m=shift_words_m)),
    ]


def gen_asm(funcs):
    print('# Auto-generated; do not edit.')
    for func in funcs:
        print()
        print(f'.global {func.name}')
        print(f'.type {func.name}, @function')
        print(f'.align 32')
        print(f'{func.name}:')
        emitter = SysvAbiFunctionEmitter()
        emitter.emit_prologue()
        func.callback(emitter)
        emitter.emit_epilogue()
        print('retq')


def parse_proto(proto_str):
    proto_str = proto_str.replace(' ', '')
    param_list, retval = proto_str.split('->')
    return param_list.split(','), retval


def proto2c_type(s, bother_with_const=True):
    s = s.replace('@', ' const ' if bother_with_const else '')
    s = s.replace('#', 'uint64_t')
    return s.strip()


def gen_c_header(funcs):
    print('''\
// Auto-generated; do not edit.
#pragma once
#include <stdint.h>
''')

    for func in funcs:
        param_list, retval = parse_proto(func.proto)

        c_param_list = ', '.join(proto2c_type(x) for x in param_list)
        c_retval = proto2c_type(retval)

        print(f'extern {c_retval} {func.name}({c_param_list});')


def gen_inline_asm(funcs):
    print('// Auto-generated; do not edit.')
    print('#pragma once')
    print('#include <stdint.h>')
    print('#include "asm_config.h"')

    for func in funcs:
        param_list, retval = parse_proto(func.proto)

        c_param_list = ', '.join(f'{proto2c_type(t)} arg{i}' for i, t in enumerate(param_list))
        c_retval = proto2c_type(retval)

        is_void = (c_retval == 'void')

        print()
        print(f'asm_attrs {c_retval} {func.name}({c_param_list})')
        print('{')
        if not is_void:
            print(f'    {c_retval} ret;')

        emitter = InlineAsmEmitter()
        emitter.emit_prologue()
        func.callback(emitter)
        emitter.emit_epilogue()

        if not is_void:
            print('    return ret;')
        print('}')


def print_usage_and_exit(msg=None):

    if msg is not None:
        print(msg, file=sys.stderr)

    print(f'''
USAGE: {sys.argv[0]} <ACTION> <WIDTH> [<FUNC_NAMES>]

Valid <ACTION>s:
 * gen_asm: print assembly to stdout
 * gen_c_header: print C header to stdout
 * gen_inline_asm: print C header with inline functions to stdout
''', file=sys.stderr)

    sys.exit(2)


def main():
    if len(sys.argv) not in [3, 4]:
        print_usage_and_exit(msg='Wrong number of arguments.')
    action = sys.argv[1]
    try:
        n = int(sys.argv[2])
    except ValueError:
        print_usage_and_exit(msg='Invalid width.')

    if action == 'gen_asm':
        gen_func = gen_asm
        is_inline_asm = False
    elif action == 'gen_c_header':
        gen_func = gen_c_header
        is_inline_asm = False
    elif action == 'gen_inline_asm':
        gen_func = gen_inline_asm
        is_inline_asm = True
    else:
        print_usage_and_exit(msg='Invalid action.')

    if len(sys.argv) == 4:
        name_filters = frozenset(sys.argv[3].split(','))
        funcs = filter(
            lambda f: f.name in name_filters,
            get_generated_funcs(n, is_inline_asm=is_inline_asm))
    else:
        funcs = get_generated_funcs(n, is_inline_asm=is_inline_asm)

    gen_func(funcs)


if __name__ == '__main__':
    main()
