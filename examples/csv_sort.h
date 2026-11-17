#ifndef CSV_SORT_H
#define CSV_SORT_H
#include <stddef.h>

typedef struct {
    char *text;
} string;

typedef struct {
    string *data;
    size_t count;     /* Number of columns */
} row;

typedef struct {
    row header;       /* First CSV row */
    row *rows;        /* CSV data rows */
    size_t count;     /* Number of data rows, excluding the header */
} rows;

void student_sort(rows *table);
#endif
