"""Profanity screening for English, Arabic (incl. Arabizi) and several other languages.
Admins can add more words from the Policy page (stored in the database)."""
import re, unicodedata

# Exact words (after normalisation). Arabic is written without diacritics; alef/ya/ta-marbuta are normalised.
WORDS = {
 'en': 'fuck fucker fucking fucked motherfucker shit shitty bullshit bitch bastard asshole arsehole dickhead cunt whore slut wanker twat prick bollocks nigger nigga retard faggot',
 'ar': 'شرموط شرموطه شراميط عرص معرص عرصه منيك منيوك منيكه كس كسم كسمك كسك كسها زب زبي طيز قحبه قحاب خول ابن_الكلب ابن_الشرموطه يلعن_ابوك كسامك متناك متناكه ديوث',
 'arz': 'sharmouta sharmota sharmoota sharmoot 3ars 3rs kos kosom kosomak kosomek koss manyak manyok manyaka ayre ayri zeb zebi tiz a7a_kosomak ya_ibn_el_sharmouta 2a7ba ka7ba kahba 5awal khawal dayouth',
 'fr': 'merde putain connard connasse salope encule enculer foutre nique niquer pute batard',
 'es': 'mierda puta puto cabron pendejo pendeja joder gilipollas hijueputa culero chingar chingada',
 'de': 'scheisse scheiße arschloch fotze wichser hurensohn schlampe fick ficken',
 'it': 'cazzo merda stronzo vaffanculo puttana troia minchia',
 'tr': 'orospu siktir amk piç pic yarrak sikerim ananı',
 'hi': 'madarchod behenchod bhosdike bhosdi chutiya gandu randi harami',
 'ru': 'блять блядь сука хуй пизда ебать пидор мудак',
}
ROOTS = ('fuck', 'shit', 'bitch', 'cunt', 'asshole', 'bastard', 'شرموط', 'منيك', 'معرص', 'قحب', 'sharmou', 'sharmo', 'motherf', 'bullshit', 'madarch', 'behench', 'enculer', 'hijueputa')
LEET = str.maketrans({'@': 'a', '4': 'a', '0': 'o', '1': 'i', '!': 'i', '3': 'e', '$': 's', '5': 's', '7': 't', '+': 't', '€': 'e', '|': 'i', '¡': 'i'})
PREFIX = ('وال', 'بال', 'فال', 'كال', 'لل', 'ال', 'و', 'ف', 'ب', 'ك', 'ل', 'ي', 'يا')

def norm(t):
    t = unicodedata.normalize('NFKD', str(t).lower())
    t = ''.join(c for c in t if not unicodedata.combining(c)).replace('ـ', '')
    return t.replace('ى', 'ي').replace('ة', 'ه')

def c3(s): return re.sub(r'(.)\1{2,}', r'\1', s)
def c2(s): return re.sub(r'(.)\1+', r'\1', s)

_cache = {}
def _build(extra):
    key = tuple(sorted(extra))
    if key in _cache: return _cache[key]
    exact = {norm(w).replace('_', ' ') for ws in WORDS.values() for w in ws.split()} | {norm(w) for w in extra}
    exact |= {w.replace(' ', '') for w in exact}
    loose = {c2(w) for w in exact if len(c2(w)) >= 4}
    _cache[key] = (exact, loose); return _cache[key]

def _hit(tok, exact, loose):
    for form in {tok, c3(tok)}:
        if form in exact: return True
        if any(form.startswith(r) for r in ROOTS): return True
    return c2(tok) in loose

def check(text, extra=()):
    """Return the offending word, or None."""
    if not text: return None
    exact, loose = _build(extra)
    base = norm(text)
    for variant in (base, base.translate(LEET)):
        joined = re.sub(r'[\W_]+', '', variant)
        toks = re.findall(r'[\w]+', variant)
        # phrases such as "ابن الكلب" / "ya ibn el sharmouta" and spaced-out letters like "f u c k"
        for tok in toks:
            cands = {tok}
            for p in PREFIX:
                if tok.startswith(p) and len(tok) - len(p) >= 2: cands.add(tok[len(p):])
            if any(_hit(c, exact, loose) for c in cands): return tok
        spaced = re.findall(r'(?:\b\w[\s._\-*]+){3,}\w\b', variant)
        for s in spaced:
            letters = re.sub(r'[\W_]+', '', s)
            for i in range(max(1, len(letters) - 3)):
                if _hit(letters[i:], exact, loose): return letters[i:]
        for phrase in [w for w in exact if ' ' in w]:
            if phrase in re.sub(r'\s+', ' ', variant): return phrase
    return None
