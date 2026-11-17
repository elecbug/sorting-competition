#define _POSIX_C_SOURCE 200809L
#include "csv_sort.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t read_u32(void) {
    uint32_t value;
    if (fread(&value, sizeof(value), 1, stdin) != 1) exit(71);
    return value;
}

static row read_row(size_t columns) {
    row r = {.data = calloc(columns, sizeof(string)), .count = columns};
    if (!r.data) exit(72);
    for (size_t c = 0; c < columns; c++) {
        uint32_t length = read_u32();
        if (length > 16384) exit(71);
        r.data[c].text = malloc((size_t)length + 1);
        if (!r.data[c].text) exit(72);
        if (fread(r.data[c].text, 1, length, stdin) != length) exit(71);
        r.data[c].text[length] = '\0';
    }
    return r;
}

static void write_u32(uint32_t value) {
    if (fwrite(&value, sizeof(value), 1, stdout) != 1) exit(73);
}

static void write_row(row r, size_t columns) {
    if (r.count != columns) exit(70);
    for (size_t c = 0; c < columns; c++) {
        size_t length = strnlen(r.data[c].text, 16385);
        if (length > 16384) exit(70);
        write_u32((uint32_t)length);
        if (fwrite(r.data[c].text, 1, length, stdout) != length) exit(73);
    }
}

int main(void) {
    uint32_t columns = read_u32(), count = read_u32();
    if (!columns || columns > 64 || count > 100000 || (size_t)(count + 1) * columns > 2000000) return 71;
    rows table = {.header = read_row(columns), .rows = calloc(count ? count : 1, sizeof(row)), .count = count};
    if (!table.rows) return 72;
    for (size_t r = 0; r < count; r++) table.rows[r] = read_row(columns);
    student_sort(&table);
    if (table.count != count) return 70;
    write_u32(columns);
    write_u32(count);
    write_row(table.header, columns);
    for (size_t r = 0; r < count; r++) write_row(table.rows[r], columns);
    /* OS가 회수합니다. 학생이 저장 방식을 바꾸어도 잘못된 free를 하지 않습니다. */
    return fflush(stdout) == 0 ? 0 : 73;
}
