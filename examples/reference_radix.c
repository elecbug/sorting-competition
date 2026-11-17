#include "csv_sort.h"
#include <stdlib.h>
/* Instructor reference for the five-byte camera key in column 2. */
void student_sort(rows *table){
    size_t n=table->count;
    if(n<2)return;
    row *buffer=malloc(n*sizeof(row));if(!buffer)return;
    row *source=table->rows,*target=buffer;
    for(int position=4;position>=0;position--){
        size_t count[256]={0},offset[256];
        for(size_t i=0;i<n;i++)count[(unsigned char)source[i].data[1].text[position]]++;
        size_t sum=0;
        for(size_t c=0;c<256;c++){offset[c]=sum;sum+=count[c];}
        for(size_t i=0;i<n;i++){unsigned char key=(unsigned char)source[i].data[1].text[position];target[offset[key]++]=source[i];}
        row *swap=source;source=target;target=swap;
    }
    if(source!=table->rows)for(size_t i=0;i<n;i++)table->rows[i]=source[i];
    free(buffer);
}
