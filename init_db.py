import sqlite3

def create_table():
    # 1. 창고 문 열기 (파일이 없으면 'my_books.db'라는 이름으로 새로 만듭니다)
    conn = sqlite3.connect('my_books.db')
    cursor = conn.cursor()

    # 2. 'reviews'라는 이름의 표(Table) 만들기
    # IF NOT EXISTS: 만약 이미 표가 있다면 새로 만들지 말라는 똑똑한 명령어입니다.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            isbn TEXT NOT NULL,
            title TEXT NOT NULL,
            author TEXT,
            cover TEXT,
            review_text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 3. 변경사항 저장하고 창고 문 닫기
    conn.commit()
    conn.close()
    print("✅ 성공! 'my_books.db' 데이터베이스와 감상문 테이블이 완벽하게 생성되었습니다.")

if __name__ == '__main__':
    create_table()