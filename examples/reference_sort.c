#include "csv_sort.h"
#include <stdlib.h>
#include <string.h>

static int compare(const void *a, const void *b) {
    const row *x = a, *y = b;
    /* Default sample rule: compare every column from left to right. */
    for (size_t c = 0; c < x->count; c++) {
        int result = strcmp(x->data[c].text, y->data[c].text);
        if (result) return result;
    }
    return 0;
}

void student_sort(rows *table) {
    qsort(table->rows, table->count, sizeof(row), compare);
}
