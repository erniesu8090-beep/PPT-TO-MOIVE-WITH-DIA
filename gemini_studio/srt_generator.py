import re

def format_timestamp(seconds: float) -> str:
    millis = int(round((seconds - int(seconds)) * 1000))
    total_sec = int(seconds)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def split_into_clauses(text: str):
    """
    Split text by common punctuation and newlines into readable subtitle segments.
    """
    cleaned = text.strip()
    if not cleaned:
        return []
    
    # Split by commas, periods, exclamation, question marks, semicolons, or line breaks
    # Keep the punctuation attached or clean it
    raw_clauses = re.findall(r'[^，。；：？！\n\r]+[，。；：？！]?', cleaned)
    clauses = [c.strip() for c in raw_clauses if c.strip()]
    if not clauses:
        clauses = [cleaned]
    return clauses

def generate_srt(page_data_list, pad_time: float = 0.5) -> str:
    """
    page_data_list is a list of dicts:
    [
      {
        "page": 1,
        "text": "第一頁台詞...",
        "duration": 8.5
      },
      ...
    ]
    pad_time: pause duration in seconds between slides
    Returns formatted SRT string.
    """
    srt_lines = []
    item_index = 1
    current_time = 0.0

    for p in page_data_list:
        text = p.get("text", "")
        duration = float(p.get("duration", 5.0))
        clauses = split_into_clauses(text)

        if not clauses:
            current_time += duration + pad_time
            continue

        total_chars = sum(len(c) for c in clauses) or 1
        clause_start = current_time

        for idx, clause in enumerate(clauses):
            if idx == len(clauses) - 1:
                clause_end = current_time + duration
            else:
                clause_dur = duration * (len(clause) / total_chars)
                clause_end = clause_start + clause_dur

            start_str = format_timestamp(clause_start)
            end_str = format_timestamp(clause_end)

            srt_lines.append(str(item_index))
            srt_lines.append(f"{start_str} --> {end_str}")
            srt_lines.append(clause)
            srt_lines.append("") # empty line separator

            item_index += 1
            clause_start = clause_end

        current_time += duration + pad_time

    return "\n".join(srt_lines)

