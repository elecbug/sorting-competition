#!/usr/bin/env python3
"""Stage three validated assignments for atomic installation on the next app start."""
import csv
import io
import json
from pathlib import Path
import random
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.config import Settings
from app.db import Database
from judge.datasets import validate_pair

SET_ID='sorting-course-v1'

def csv_text(header, rows):
    stream=io.StringIO(newline=''); writer=csv.writer(stream);writer.writerow(header);writer.writerows(rows);return stream.getvalue()

def read(path):
    with path.open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.reader(stream);return next(reader),list(reader)

def unique_rows(rows,column,predicate=lambda value:True):
    found=set(); result=[]
    for row in rows:
        value=row[column]
        if value not in found and predicate(value):
            found.add(value);result.append(row)
    return result

def statements(kind, column, header, count, columns):
    names={
      'stable': ['Stable sort with minimal memory','메모리를 최소화하는 안정정렬','最小内存稳定排序','Sắp xếp ổn định, ít bộ nhớ','Стабильная сортировка с малой памятью'],
      'fast': ['Fast sorting of place names','금연구역명 고속 정렬','禁烟场所名称快速排序','Sắp xếp nhanh tên địa điểm','Быстрая сортировка названий'],
      'radix': ['Fast sorting of fixed-width camera IDs','고정 길이 카메라 번호 고속 정렬','固定长度摄像头编号快速排序','Sắp xếp nhanh mã camera cố định','Быстрая сортировка кодов камер']}
    en=f'Sort by column {column} ({header}), C index {column-1}, ascending by unsigned UTF-8 bytes. Write your own comparison code. Preserve the header and all complete rows.\n'
    ko=f'{column}번 칼럼({header}, C 인덱스 {column-1})을 unsigned UTF-8 바이트 기준 오름차순으로 정렬하세요. 비교 함수는 직접 구현하고 헤더와 각 행의 내용을 보존하세요.\n'
    zh=f'按第 {column} 列（{header}，C 下标 {column-1}）的无符号 UTF-8 字节升序排序。自行实现比较，保留表头和完整行内容。\n'
    vi=f'Sắp xếp cột {column} ({header}, chỉ số C {column-1}) tăng dần theo byte UTF-8 không dấu. Tự viết so sánh, giữ nguyên tiêu đề và nội dung hàng.\n'
    ru=f'Сортируйте по столбцу {column} ({header}, индекс C {column-1}) по возрастанию беззнаковых байтов UTF-8. Реализуйте сравнение самостоятельно; сохраните заголовок и строки.\n'
    if kind=='stable':
        extra=['Stability is required: rows with equal keys must retain their order in each input file. Minimize memory. Correctness 60, memory 40, time 0. Ranking uses Peak RSS; it includes the provided input structure.', '안정정렬이 필수입니다. 키가 같은 행은 각 원본 파일에서의 상대 순서를 유지하세요. 메모리를 최소화하세요. 정확성 60점, 메모리 40점, 시간 0점이며 입력 구조를 포함한 Peak RSS로 순위를 매깁니다.', '必须稳定：相同键的行保持各输入文件中的相对顺序。尽量减少内存。正确性60分，内存40分，时间0分。按包含输入结构的峰值RSS排名。', 'Phải ổn định: giữ thứ tự ban đầu của các hàng có khóa bằng nhau trong từng tệp. Giảm bộ nhớ. Độ đúng 60, bộ nhớ 40, thời gian 0; xếp hạng Peak RSS gồm cấu trúc đầu vào.', 'Сортировка должна быть стабильной: равные ключи сохраняют порядок каждого входного файла. Минимизируйте память. Корректность 60, память 40, время 0. Рейтинг по Peak RSS, включая входные данные.']
    else:
        extra=['Keys are unique in every test, so stability is not required. Optimize CPU time. Correctness 60, time 40, memory 0.', '각 테스트에서 키는 중복되지 않으므로 안정정렬은 필수가 아닙니다. CPU 시간을 최소화하세요. 정확성 60점, 시간 40점, 메모리 0점입니다.', '每个测试中的键均唯一，不要求稳定排序。优化CPU时间。正确性60分，时间40分，内存0分。', 'Khóa không trùng trong mỗi kiểm thử, không bắt buộc ổn định. Giảm thời gian CPU. Độ đúng 60, thời gian 40, bộ nhớ 0.', 'Ключи каждого теста уникальны, стабильность не требуется. Минимизируйте время CPU. Корректность 60, время 40, память 0.']
    if kind=='radix':
        hints=[' Every key has exactly five ASCII bytes: one uppercase letter and four decimal digits.', ' 모든 키는 대문자 1개와 숫자 4개로 구성된 정확히 5바이트의 ASCII 문자열입니다.', ' 每个键恰好5个ASCII字节：一个大写字母加四位十进制数字。', ' Mỗi khóa có đúng 5 byte ASCII: một chữ hoa và 4 chữ số.', ' Каждый ключ — ровно 5 байт ASCII: заглавная буква и четыре цифры.']
        extra=[a+b for a,b in zip(extra,hints)]
    result={}
    for language,title,body,tail in zip(('en','ko','zh','vi','ru'),names[kind],(en,ko,zh,vi,ru),extra):
        result[language]={'title':title,'description':body+'\n'+tail+f'\n\n{count:,} rows / {columns} columns.'}
    return result

def stage(settings):
    pending=settings.data_dir/'pending_problem_set.json'
    if pending.exists():
        print('A validated problem set is already staged. Keeping it unchanged.');return json.loads(pending.read_text())
    samples=settings.data_dir/'sample_csv'
    specs=[]
    h,r=read(samples/'전국신호등표준데이터.csv')
    specs.append(('stable',h,r,1,'전국신호등표준데이터.csv'))
    h,r=read(samples/'전국금연구역표준데이터.csv')
    specs.append(('fast',h,unique_rows(r,0),0,'전국금연구역표준데이터.csv'))
    h,r=read(samples/'전국무인교통단속카메라표준데이터.csv')
    r=unique_rows(r,0,lambda value:bool(re.fullmatch('[A-Z][0-9]{4}',value)))
    projected=[h.index('시도명'),0,h.index('설치장소')]
    specs.append(('radix',[h[i] for i in projected],[[row[i] for i in projected] for row in r],1,'전국무인교통단속카메라표준데이터.csv'))
    manifest={'set_id':SET_ID,'problems':[]}
    database=Database(settings.data_dir)
    with database.connect() as db:
        manifest['replace']=[dict(row) for row in db.execute("SELECT id,title FROM problems WHERE title LIKE '[Sample]%' OR title LIKE '[샘플]%'")]
    for index,(kind,header,rows,column,source) in enumerate(specs,1):
        if not rows:raise ValueError(f'No suitable source rows: {source}')
        directory=settings.data_dir/'generated_problems'/kind
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        texts=statements(kind,column+1,header[column],len(rows),len(header));base=texts.pop('en')
        problem={**base,'translations':texts,'scoring_mode':'memory' if kind=='stable' else 'time','is_published':False,'source':source,'column':column+1,'stable':kind=='stable','rows':len(rows),'columns':len(header),'cases':[]}
        for label,public,seed,subset in [('Public input',True,100+index,None),('Hidden shuffle',False,900+index,None),('Hidden edge cases',False,1700+index,256)]:
            values=list(rows[:subset] if subset else rows)
            random.Random(seed).shuffle(values)
            answer=sorted(values,key=lambda row:row[column].encode('utf-8'))
            original_text,answer_text=csv_text(header,values),csv_text(header,answer)
            validate_pair(original_text,answer_text)
            case={'title':label,'is_public':public}
            for key,text in [('input_file',original_text),('answer_file',answer_text)]:
                path=directory/(label.lower().replace(' ','_')+('.input.csv' if key=='input_file' else '.answer.csv'))
                path.write_bytes(text.encode());path.chmod(0o600);case[key]=str(path.relative_to(settings.data_dir))
            problem['cases'].append(case)
        manifest['problems'].append(problem)
        print(f'Staged {index}: {kind}, column {column+1}, {len(rows):,} rows x {len(header)} columns, unpublished')
    temporary=pending.with_suffix('.tmp')
    temporary.write_text(json.dumps(manifest,ensure_ascii=False,indent=2));temporary.chmod(0o600);temporary.replace(pending)
    return manifest

if __name__=='__main__':stage(Settings.from_env())
