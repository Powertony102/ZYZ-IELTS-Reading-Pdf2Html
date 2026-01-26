#!/usr/bin/env python3
"""Verify HTML content is complete"""

html_path = "ZYZ 1月高频（139篇+29背景）/P3（41高+9次）/1. 高频/187. P3 - Petrol power an eco-revolution 交通的革命【高】/187. P3 - Petrol power an eco-revolution 交通的革命【高】.html"

with open(html_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Check for key content
has_passage = 'READING PASSAGE' in content and 'Laura Ingalls Wilder' in content
has_questions = 'question-content' in content and 'q27' in content
has_nav = 'q-nav' in content
has_answers_json = 'const ANSWERS' in content

print(f'✓ 文章内容: {"存在" if has_passage else "缺失"}')
print(f'✓ 题目内容: {"存在" if has_questions else "缺失"}')
print(f'✓ 题目导航: {"存在" if has_nav else "缺失"}')
print(f'✓ 答案数据: {"存在" if has_answers_json else "缺失"}')
print(f'\n文件大小: {len(content):,} 字符')

# Count questions
import re
q_count = len(re.findall(r'<article class="question"', content))
print(f'题目数量: {q_count}')

# Check if content areas are empty
empty_passage = '<div id="passage-content"></div>' in content
empty_questions = '<div id="question-content"></div>' in content
print(f'\n状态: {"❌ HTML为空！" if empty_passage or empty_questions else "✅ HTML内容正常"}')
