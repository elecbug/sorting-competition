"""Fail-closed checks on GCC-preprocessed C and the student's object file."""
import ctypes
import json
from pathlib import Path
import re
import sys

ALLOWED_HEADERS = {"csv_sort.h", "stdio.h", "stdlib.h", "stddef.h", "stdint.h", "stdbool.h", "limits.h", "inttypes.h", "errno.h", "stdarg.h"}
ALLOWED_FUNCTIONS = set("""
malloc calloc realloc free aligned_alloc
fopen freopen fclose fflush setbuf setvbuf fprintf fscanf printf scanf snprintf sprintf sscanf
vfprintf vfscanf vprintf vscanf vsnprintf vsprintf vsscanf fgetc fgets fputc fputs getc getchar
putc putchar puts ungetc fread fwrite fgetpos fseek fsetpos ftell rewind clearerr feof ferror
perror remove rename tmpfile tmpnam fileno getline getdelim fseeko ftello dprintf vdprintf
__isoc99_fscanf __isoc99_scanf __isoc99_sscanf __isoc99_vfscanf __isoc99_vscanf __isoc99_vsscanf
__isoc23_fscanf __isoc23_scanf __isoc23_sscanf __isoc23_vfscanf __isoc23_vscanf __isoc23_vsscanf
__printf_chk __fprintf_chk __sprintf_chk __snprintf_chk __vprintf_chk __vfprintf_chk
__vsprintf_chk __vsnprintf_chk __errno_location
""".split())
ALLOWED_INTRINSICS = {"__builtin_va_start", "__builtin_va_end", "__builtin_va_copy", "__builtin_offsetof"}
DATA_SYMBOLS = {"stdin", "stdout", "stderr", "_GLOBAL_OFFSET_TABLE_"}
# GCC may lower struct assignment/initialization into these calls. Explicit uses
# are still rejected by AST validation before this object-level allowance.
COMPILER_SYMBOLS = {"memcpy", "memset", "memmove"}
RESERVED = ALLOWED_FUNCTIONS | DATA_SYMBOLS | COMPILER_SYMBOLS | {"main", "_start", "exit", "strnlen", "__libc_start_main"}


def validate_source(text):
    text = text.replace('??=', '#').replace('??/', '\\')
    text = re.sub(r'\\\r?\n', '', text)
    pattern = r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/'
    text = re.sub(pattern, lambda match: ' ' if match.group().startswith(('/',)) else match.group(), text, flags=re.S)
    if re.search(r'^\s*(?:#|%:)\s*(?:line\b|\d)', text, flags=re.M):
        raise ValueError("Source location directives are not permitted.")


def prepare_preprocessed(text, source_path):
    current = ""
    clean = []
    for line in text.splitlines(keepends=True):
        marker = re.match(r'^#\s+\d+\s+"([^"]+)"', line)
        if marker:
            current = marker.group(1)
        inclusion = re.match(r'^\s*#\s*include\s*[<"]([^>"\n]+)[>"]', line)
        if inclusion:
            if current == source_path and inclusion.group(1) not in ALLOWED_HEADERS:
                raise ValueError("Disallowed header: " + inclusion.group(1))
            clean.append("\n")
        else:
            clean.append(line)
    return "".join(clean)


def validate_ast(preprocessed_path, source_path):
    from clang import cindex as c
    lib = c.conf.lib
    lib.clang_getPresumedLocation.argtypes = [c.SourceLocation, ctypes.POINTER(c._CXString), ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    lib.clang_getPresumedLocation.restype = None

    def origin(cursor):
        name, line, column = c._CXString(), ctypes.c_uint(), ctypes.c_uint()
        lib.clang_getPresumedLocation(cursor.location, name, line, column)
        return c._CXString.from_result(name)

    raw = Path(preprocessed_path).read_text()
    # GCC's deallocator annotation is not understood by libclang. Remove only
    # this non-executable annotation for parsing, never from the compiled file.
    parsed = re.sub(r'__attribute__\s*\(\(\s*__malloc__\s*\([^)]*\)\s*\)\)', '', raw)
    translation = c.Index.create().parse(preprocessed_path, args=["-x", "c", "-std=c11"], unsaved_files=[(preprocessed_path, parsed)])
    if any(d.severity >= c.Diagnostic.Error for d in translation.diagnostics):
        raise ValueError("C policy validation failed: " + str(next(d for d in translation.diagnostics if d.severity >= c.Diagnostic.Error)))
    roots = [node for node in translation.cursor.get_children() if origin(node) == source_path]
    functions = {n.spelling for n in roots if n.kind == c.CursorKind.FUNCTION_DECL and n.is_definition()}
    if "student_sort" not in functions:
        raise ValueError("student_sort must be defined in the submitted source.")
    for node in roots:
        if node.kind in (c.CursorKind.FUNCTION_DECL, c.CursorKind.VAR_DECL) and node.is_definition() and node.storage_class != c.StorageClass.STATIC:
            if node.spelling in RESERVED or node.spelling.startswith("__"):
                raise ValueError("Reserved runtime symbol cannot be defined: " + node.spelling)
    forbidden = set()
    for root in roots:
        for node in root.walk_preorder():
            if node.kind == c.CursorKind.ASM_STMT or "ASM_LABEL" in node.kind.name:
                raise ValueError("Inline assembly and assembler aliases are not permitted.")
            if node.kind == c.CursorKind.CALL_EXPR:
                callee = node.referenced
                if callee is None or callee.kind != c.CursorKind.FUNCTION_DECL:
                    raise ValueError("Indirect function calls are not permitted. Call your own helper directly.")
            if node.kind == c.CursorKind.DECL_REF_EXPR and node.referenced and node.referenced.kind == c.CursorKind.FUNCTION_DECL:
                name = node.referenced.spelling
                if name not in functions and name not in ALLOWED_FUNCTIONS and name not in ALLOWED_INTRINSICS:
                    forbidden.add(name)
    if forbidden:
        raise ValueError("Disallowed library functions: " + ", ".join(sorted(forbidden)))


def validate_symbols(output):
    names = {line.split()[0].split('@')[0] for line in output.splitlines() if line.strip()}
    forbidden = names - ALLOWED_FUNCTIONS - DATA_SYMBOLS - COMPILER_SYMBOLS
    if forbidden:
        raise ValueError("Disallowed external symbols: " + ", ".join(sorted(forbidden)))


if __name__ == "__main__":
    try:
        validate_ast(sys.argv[1], sys.argv[2])
        print(json.dumps({"ok": True}))
    except Exception as error:
        print(json.dumps({"ok": False, "message": str(error)}))
