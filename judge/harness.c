#define _GNU_SOURCE
#include "csv_sort.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Each wire field is at most 16 KiB. Buffer complete fields while keeping
 * the student's rows and individually owned C strings in their public ABI. */
static unsigned char input_block[65536], output_block[65536];
static size_t input_pos, input_end, output_size;

static const unsigned char *read_field(size_t length) {
    size_t left = input_end - input_pos;
    if (left < length) {
        memmove(input_block, input_block + input_pos, left);
        input_pos = 0;
        input_end = left + fread_unlocked(input_block + left, 1, sizeof(input_block) - left, stdin);
        if (input_end < length) exit(71);
    }
    const unsigned char *field = input_block + input_pos;
    input_pos += length;
    return field;
}

static uint32_t read_u32(void) {
    uint32_t value;
    memcpy(&value, read_field(sizeof(value)), sizeof(value));
    return value;
}

static void flush_output(void) {
    if (fwrite_unlocked(output_block, 1, output_size, stdout) != output_size) exit(73);
    output_size = 0;
}

static void write_field(const void *data, size_t length) {
    if (length > sizeof(output_block) - output_size) flush_output();
    memcpy(output_block + output_size, data, length);
    output_size += length;
}

static row read_row(size_t columns) {
    row r = {.data = calloc(columns, sizeof(string)), .count = columns};
    if (!r.data) exit(72);
    for (size_t c = 0; c < columns; c++) {
        uint32_t length = read_u32();
        if (length > 16384) exit(71);
        r.data[c].text = malloc((size_t)length + 1);
        if (!r.data[c].text) exit(72);
        memcpy(r.data[c].text, read_field(length), length);
        r.data[c].text[length] = '\0';
    }
    return r;
}

static void write_u32(uint32_t value) {
    write_field(&value, sizeof(value));
}

static void write_row(row r, size_t columns) {
    if (r.count != columns) exit(70);
    for (size_t c = 0; c < columns; c++) {
        size_t length = strnlen(r.data[c].text, 16385);
        if (length > 16384) exit(70);
        write_u32((uint32_t)length);
        write_field(r.data[c].text, length);
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
    /* The OS reclaims memory, avoiding invalid frees if the student changes storage. */
    flush_output();
    return fflush(stdout) == 0 ? 0 : 73;
}
