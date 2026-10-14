"""Shared Chinese subtitle limits for the slide and podcast pipelines."""
import math
import re

MAX_LINE_CHARS = 18
MAX_EVENT_CHARS = 26
MAX_EVENT_SECONDS = 5.0


def split_srt_by_punctuation(text, target_len=MAX_EVENT_CHARS):
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return []
    target_len = max(1, min(int(target_len), MAX_LINE_CHARS * 2))
    phrases = re.findall(r'[^。！？!?；;，,、：:]+[。！？!?；;，,、：:]*|[。！？!?；;，,、：:]+', text)
    chunks, current = [], ''
    for phrase in phrases:
        while len(phrase) > target_len:
            if current:
                chunks.append(current)
                current = ''
            chunks.append(phrase[:target_len])
            phrase = phrase[target_len:]
        if len(current) + len(phrase) > target_len:
            chunks.append(current)
            current = ''
        current += phrase
    if current:
        chunks.append(current)
    return chunks


def wrap_ass_lines(text, max_chars=MAX_LINE_CHARS):
    """Wrap one validated event, never producing a third line."""
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > max_chars * 2:
        raise ValueError('字幕事件過長，請先切分時間事件。')
    if len(text) <= max_chars:
        return text
    lower = max(1, len(text) - max_chars)
    cut = math.ceil(len(text) / 2)
    for index in range(max_chars - 1, lower - 2, -1):
        if text[index] in '，、；。？！ ,:：':
            cut = index + 1
            break
    return text[:cut].rstrip() + r'\N' + text[cut:].lstrip()


def ass_text(text):
    # Display literal punctuation instead of interpreting user text as ASS tags.
    return text.replace('\\', '＼').replace('{', '｛').replace('}', '｝')


def split_timed_text(text, start, end, prefix=''):
    """Keep original endpoints, limit chars/duration, label only the first event."""
    if end <= start:
        return []
    chunks = split_srt_by_punctuation(text)
    if not chunks:
        return []
    prefix = re.sub(r'\s+', ' ', prefix).strip()
    if len(prefix) > MAX_LINE_CHARS:
        prefix = prefix[:MAX_LINE_CHARS - 1] + '…'
    if prefix:
        capacity = max(1, MAX_EVENT_CHARS - len(prefix) - 1)
        first = chunks.pop(0)
        pieces = split_srt_by_punctuation(first, capacity)
        chunks = pieces + chunks
    # Weight only spoken text, not the displayed speaker label.
    weights = [max(1, len(chunk)) for chunk in chunks]
    total = sum(weights)
    cursor, events = start, []
    for i, (chunk, weight) in enumerate(zip(chunks, weights)):
        chunk_end = end if i == len(chunks) - 1 else cursor + (end - start) * weight / total
        count = max(1, math.ceil((chunk_end - cursor) / MAX_EVENT_SECONDS))
        # Prefer splitting text further to repeating it across long intervals.
        count = min(count, len(chunk))
        parts = [chunk[j * len(chunk) // count:(j + 1) * len(chunk) // count] for j in range(count)]
        part_start = cursor
        for j, part in enumerate(parts):
            part_end = chunk_end if j == count - 1 else cursor + (chunk_end - cursor) * (j + 1) / count
            intervals = max(1, math.ceil((part_end - part_start) / MAX_EVENT_SECONDS))
            for k in range(intervals):
                events.append({
                    'text': prefix + ' ' + part if prefix and not events else part,
                    'start': part_start + (part_end - part_start) * k / intervals,
                    'end': part_end if k == intervals - 1 else part_start + (part_end - part_start) * (k + 1) / intervals,
                })
            part_start = part_end
        cursor = chunk_end
    return events


def format_srt_time(seconds):
    milliseconds = max(0, round(seconds * 1000))
    seconds, milliseconds = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def format_ass_time(seconds):
    centiseconds = max(0, round(seconds * 100))
    seconds, centiseconds = divmod(centiseconds, 100)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"
