#include "csv_sort.h"
#include <stdlib.h>
#ifndef SORT_COLUMN
#define SORT_COLUMN (-1)
#endif
static int text_compare(const char *a, const char *b) {
    while (*a && (unsigned char)*a == (unsigned char)*b) { ++a; ++b; }
    return (unsigned char)*a - (unsigned char)*b;
}
static int compare_rows(const row *a, const row *b) {
    if (SORT_COLUMN >= 0) return text_compare(a->data[SORT_COLUMN].text, b->data[SORT_COLUMN].text);
    for (size_t c=0;c<a->count;c++) { int r=text_compare(a->data[c].text,b->data[c].text); if(r)return r; }
    return 0;
}
void student_sort(rows *table) {
    size_t n=table->count;
    if(n<2)return;
    row *buffer=malloc(n*sizeof(row));
    if(!buffer)return;
    row *source=table->rows,*target=buffer;
    for(size_t width=1;width<n;width*=2){
        for(size_t left=0;left<n;left+=2*width){
            size_t middle=left+width<n?left+width:n,end=left+2*width<n?left+2*width:n;
            size_t a=left,b=middle,out=left;
            while(a<middle&&b<end)target[out++]=compare_rows(&source[a],&source[b])<=0?source[a++]:source[b++];
            while(a<middle)target[out++]=source[a++];
            while(b<end)target[out++]=source[b++];
        }
        row *swap=source;source=target;target=swap;
    }
    if(source!=table->rows)for(size_t i=0;i<n;i++)table->rows[i]=source[i];
    free(buffer);
}
