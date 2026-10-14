"""The same ASS styles are used for final videos and real frame previews."""
import math

COLORS = {'white': '#ffffff', 'yellow': '#ffff00', 'cyan': '#40a9ff',
          'green': '#a0d911', 'gray': '#cccccc'}


def number(value, default, low, high):
    try:
        value = float(value)
        return max(low, min(high, value)) if math.isfinite(value) else default
    except (ValueError, TypeError):
        return default


def hex_to_ass(value, fallback='#ffffff'):
    value = str(value or fallback).lstrip('#')
    if len(value) != 6 or any(c not in '0123456789abcdefABCDEF' for c in value):
        value = fallback.lstrip('#')
    return f'&H00{value[4:6]}{value[2:4]}{value[0:2]}&'


def style_values(settings):
    return {
        'font_name': str(settings.get('font_name', 'Microsoft JhengHei')).replace(',', '').replace('\n', '').replace('\r', ''),
        'font_size': int(number(settings.get('font_size'), 32, 18, 60)),
        'font_color': COLORS.get(settings.get('font_color'), '#ffffff'),
        'bg_style': 'box' if settings.get('bg_style') == 'box' else 'outline',
        'bg_opacity': number(settings.get('bg_opacity'), .75, 0, 1),
        'bottom_pos': int(number(settings.get('bottom_pos'), 50, 10, 150)),
        'safe_margin': max(420, round(1920 * (1 - number(settings.get('max_width'), 56, 30, 56) / 100) / 2)),
    }


def ass_header(settings, voice_settings):
    values = style_values(settings)
    box = values['bg_style'] == 'box'
    back = f"&H{round((1-values['bg_opacity'])*255):02X}000000&" if box else '&H80000000&'
    styles = []
    colors = {'Default': values['font_color'],
              'SpeakerA': voice_settings.get('speaker_a', {}).get('color', '#40a9ff'),
              'SpeakerB': voice_settings.get('speaker_b', {}).get('color', '#fadb14')}
    for name, color in colors.items():
        styles.append(f"Style: {name},{values['font_name']},{values['font_size']},{hex_to_ass(color)},&H000000FF&,&H00000000&,{back},-1,0,0,0,100,100,0,0,{3 if box else 1},{3 if box else 2},{0 if box else 2},2,{values['safe_margin']},{values['safe_margin']},{values['bottom_pos']},1")
    return '\n'.join([
        '[Script Info]', 'ScriptType: v4.00+', 'WrapStyle: 2', 'ScaledBorderAndShadow: yes',
        'PlayResX: 1920', 'PlayResY: 1080', '', '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
        *styles, '', '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text', ''])
