#ifndef CSV_SORT_H
#define CSV_SORT_H
#include <stddef.h>

typedef struct {
    char *text;
} string;

typedef struct {
    string *data;
    size_t count;     /* 열 개수 */
} row;

typedef struct {
    row header;       /* CSV 첫 번째 행 */
    row *rows;        /* CSV 데이터 행 */
    size_t count;     /* 데이터 행 개수 (헤더 제외) */
} rows;

void student_sort(rows *table);
#endif
