from __future__ import annotations

import difflib
import re

from .schemas import HistoryMessage

def _clean_reply(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return "..."

    # Remove identical lines and obvious prompt-artifact lines.
    seen: set[str] = set()
    kept: list[str] = []
    for line in raw.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        low = candidate.lower()
        if re.search(r"\b(chatbot|ai|model|system|prompt|policy|architecture|llama)\b", low):
            continue
        if low.startswith(("always ", "never ", "do not ", "responses must ")):
            continue
        if re.search(
            r"\b(how are you today|what brings you here|what brings me here|what's new|what is new|how was your day)\b",
            low,
        ):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        kept.append(candidate)

    collapsed = " ".join(kept) if kept else raw
    collapsed = re.sub(r"\s+", " ", collapsed).strip()
    if not collapsed:
        return "..."

    # Keep response compact and avoid long repetitive continuations.
    chunks = re.split(r"(?<=[.!?])\s+", collapsed)
    short = " ".join(chunks[:2]).strip() if chunks else collapsed
    return (short[:420]).strip() or "..."


def _looks_hindi_intent(text: str) -> bool:
    low = text.lower()
    if re.search(r"[\u0900-\u097f]", text):
        return True
    return bool(
        re.search(
            (
                r"\b(bhai|bro|brother|yar|yaar|kaise|kya|nahi|nhi|haan|achha|acha|"
                r"tum|tumhe|mujhe|meri|mera|mere|hai|ho|karna|krna|kuch|batao|"
                r"baat|bat|sikha|sikhao|sikha do|thik|theek|thick|hindi|"
                r"hoon|raha|rahi|rahe|karu|karo|chahiye|gaya|gayi|bolo|sunao|"
                r"ladki|impress|sarcasm|yaad|samajh)\b"
            ),
            low,
        )
    )


def _hindi_marker_hits(text: str) -> int:
    low = text.lower()
    return len(
        re.findall(
            (
                r"\b(hai|haan|nahi|nhi|tum|tumhe|aap|bhai|bro|kaise|kya|acha|theek|"
                r"krna|karna|samjha|chalo|sawal|jawab|main|mujhe|mera|mere|apna|karo|"
                r"bilkul|madad|bolo|pucho|samasya|karunga|karungi|seedha|rahe|ho|"
                r"yar|yaar|kuch|batao|bat|baat|sikha|sikhao|thik|thick|"
                r"hoon|raha|rahi|karu|gaya|gayi|chahiye|sunao|ladki|impress|yaad|samajh)\b"
            ),
            low,
        )
    )


def _english_token_hits(text: str) -> int:
    return len(re.findall(r"[a-zA-Z]{3,}", text))


def _english_clue_hits(text: str) -> int:
    low = text.lower()
    return len(
        re.findall(
            (
                r"\b(i|i'm|im|you|your|my|we|they|is|are|the|and|what|how|why|"
                r"when|where|please|can|could|would|from|name|today|hello)\b"
            ),
            low,
        )
    )


def _looks_informational_question(text: str) -> bool:
    low = (text or "").lower()
    if not low.strip():
        return False
    if "?" in low:
        return True
    return bool(
        re.search(
            (
                r"\b(kisne|kaise|kyu|kyun|kab|kahan|kitne|kya|what|how|why|"
                r"when|who|which|where)\b"
            ),
            low,
        )
    )


def _explicit_hi_switch(text: str) -> bool:
    low = text.lower()
    return bool(
        re.search(
            (
                r"\b(only\s+hindi|hindi\s+mein|hindi\s+mai|sirf\s+hindi|hindi me|"
                r"hindi only|hindi\s+mein baat|hindi mai bat|hindi me bat|"
                r"hindi mai baat|hindi me baat)\b"
            ),
            low,
        )
    )


def _explicit_en_switch(text: str) -> bool:
    low = text.lower()
    return bool(
        re.search(
            (
                r"\b(only\s+english|english\s+mein|english\s+mai|english me|in english|"
                r"english only|english mai baat|english me baat|english mai bat|english me bat)\b"
            ),
            low,
        )
    )


def _detect_language_mode(msg: str, history: list[HistoryMessage]) -> str:
    # 1) Current user message has highest priority.
    if _explicit_en_switch(msg):
        return "en"
    if _explicit_hi_switch(msg):
        return "hi"

    # 2) Strong language signal in current message.
    if re.search(r"[\u0900-\u097f]", msg):
        return "hi"
    msg_hi_hits = _hindi_marker_hits(msg)
    msg_en_clues = _english_clue_hits(msg)
    if msg_hi_hits >= 2:
        return "hi"
    if msg_en_clues >= 2 and msg_hi_hits == 0:
        return "en"
    if msg_hi_hits >= 1 and _looks_hindi_intent(msg):
        return "hi"

    # 3) Recent user preference/signals in history (for ambiguous messages).
    for item in reversed(history[-20:]):
        if item.role != "user":
            continue
        if _explicit_en_switch(item.content):
            return "en"
        if _explicit_hi_switch(item.content):
            return "hi"
        if re.search(r"[\u0900-\u097f]", item.content):
            return "hi"
        hi_hits = _hindi_marker_hits(item.content)
        en_clues = _english_clue_hits(item.content)
        if hi_hits >= 2:
            return "hi"
        if en_clues >= 2 and hi_hits == 0:
            return "en"

    # 4) Fallback heuristic by latest message text.
    if _looks_hindi_intent(msg):
        return "hi"
    return "en"


def _intent_hint(msg: str, lang_mode: str) -> str:
    low = msg.lower()
    if re.search(r"\b(problem|issue|error|fix|not working|bug)\b", low):
        if lang_mode == "hi":
            return "Issue mode: ek practical step do, phir ek exact detail mango."
        return "Issue mode: give one practical troubleshooting step, then ask for one exact detail."
    if re.search(
        r"\b(playful|tease|mystery|mysterious|interesting|confident|charming|surprising|fresh|emotional|weight)\b",
        low,
    ):
        if lang_mode == "hi":
            return "Conversation style mode: natural aur human line do, assistant/helpdesk tone mat lao."
        return "Conversation style mode: give a natural human line, no assistant/helpdesk tone."
    if re.search(r"\b(ladki|girl|impress|date|crush|patana|patao|set)\b", low):
        if lang_mode == "hi":
            return "Respectful dating-advice mode: practical, safe, and non-creepy tips do."
        return "Respectful dating-advice mode: practical, safe, and non-creepy tips."
    if re.search(r"\b(bad|sad|upset|down|very bad|pareshan|dukhi)\b", low):
        if lang_mode == "hi":
            return "Support mode: chhoti empathy line + ek actionable step do."
        return "Support mode: brief empathy + one actionable next step."
    if re.search(r"\b(hello|hi|hey|namaste)\b", low):
        if lang_mode == "hi":
            return "Greeting mode: short warm greeting, then direct value."
        return "Greeting mode: short warm greeting, then direct value."
    return "Answer mode: be direct, specific, and concise."


def _human_response_hint(msg: str, lang_mode: str) -> str:
    low = (msg or "").lower()
    if "?" in msg or re.search(r"\b(kya|kaise|kyu|why|how|what|when)\b", low):
        if lang_mode == "hi":
            return "Insani tone rakho: pehle seedha jawab do, fir optional short follow-up."
        return "Keep a human tone: answer first, then optional short follow-up."
    if re.search(r"\b(sad|bad|tired|dukhi|pareshan|very bad)\b", low):
        if lang_mode == "hi":
            return "Empathy do, lekin practical aur short raho."
        return "Show brief empathy, then be practical and concise."
    if lang_mode == "hi":
        return "Natural insani flow rakho, scripted line mat do."
    return "Keep a natural human flow, avoid scripted phrasing."


def _build_memory_summary(
    history: list[HistoryMessage],
    *,
    max_user_turns: int,
    max_items: int,
) -> str:
    if not history:
        return ""

    user_msgs: list[str] = []
    lang_pref = ""
    for item in reversed(history[-(max_user_turns * 2 + 20) :]):
        if item.role != "user":
            continue
        content = item.content.strip()
        if not content:
            continue
        if not lang_pref:
            if _explicit_hi_switch(content):
                lang_pref = "User recently asked for Hindi replies."
            elif _explicit_en_switch(content):
                lang_pref = "User recently asked for English replies."
        user_msgs.append(content)
        if len(user_msgs) >= max_user_turns:
            break

    if not user_msgs:
        return lang_pref

    user_msgs.reverse()
    cleaned: list[str] = []
    seen: set[str] = set()
    for msg in user_msgs[-max_items:]:
        compact = re.sub(r"\s+", " ", msg).strip()
        if not compact:
            continue
        short = compact[:100]
        low = short.lower()
        if low in seen:
            continue
        seen.add(low)
        cleaned.append(short)

    if not cleaned:
        return lang_pref

    context_line = "Recent user context: " + " | ".join(cleaned)
    if lang_pref:
        return f"{lang_pref} {context_line}"
    return context_line


def _looks_prompt_override(text: str) -> bool:
    low = (text or "").lower()
    if len(low) < 140:
        return False
    signals = 0
    patterns = [
        "you are now",
        "you are not acting",
        "taking on the identity",
        "identity of a fictional personality",
        "behavior rules",
        "language behavior",
        "emotional adaptation",
        "human realism layer",
        "creativity control",
        "conversation flow",
        "generate all replies",
        "assistant:",
        "user:",
        "do not",
    ]
    for p in patterns:
        if p in low:
            signals += 1
    if "assistant:" in low and "user:" in low:
        signals += 2
    if low.count("do not") >= 4:
        signals += 1
    return signals >= 3


def _choose_variant(options: list[str], key: str, avoid: str = "") -> str:
    if not options:
        return ""
    idx = sum(ord(c) for c in key) % len(options)
    avoid_low = (avoid or "").strip().lower()
    for shift in range(len(options)):
        candidate = options[(idx + shift) % len(options)]
        if candidate.strip().lower() != avoid_low:
            return candidate
    return options[idx]


def _hindi_fallback_by_intent(user_msg: str, avoid: str = "") -> str:
    low = (user_msg or "").lower()
    avoid_low = (avoid or "").lower()
    compact = re.sub(r"[^a-z0-9\s]", " ", low).strip()
    word_count = len([w for w in compact.split() if w])
    if _explicit_hi_switch(user_msg) or "hindi" in low:
        return _choose_variant(
            [
                "Bilkul, ab se main Roman Hindi me hi short aur clear jawab dunga.",
                "Theek hai, ab hum Roman Hindi me seedhi baat karenge.",
                "Done, ab se reply sirf Roman Hindi me aayega.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(ready ho|ready)\b", low) and re.search(r"^\s*(hi|hello|hey)\b", low):
        return "Haan ready hoon. Bolo kis topic se start karna hai."
    if re.search(r"\b(yaad rakh|previous chat|memory)\b", low):
        return "Main current chat context ko use karke continuity rakh sakta hoon. Long-term memory ke liye explicit storage layer chahiye hoti hai."
    if re.search(r"\b(calm tone|panic mode)\b", low):
        return "Theek hai, hum calm aur steady tone me baat karenge. Main short, clear, aur non-panic style me reply dunga."
    if re.search(r"\b(mobile).*\b(invention|invent)\b", low) or re.search(
        r"\b(invention|invent).*\b(mobile)\b", low
    ):
        return "Commercial mobile phone ka credit Martin Cooper ko diya jata hai. Unhone 1973 me first handheld mobile call ki thi."
    if re.search(r"\b(js|javascript)\b", low) and re.search(
        r"\b(sikhu|seekhu|kaise|learn)\b", low
    ):
        return "JS sikhne ke liye basics se start karo: variables, functions, DOM, aur async. Roz chhote projects banao, learning fast ho jayegi."
    if re.search(r"\b(js|javascript)\b", low) and re.search(
        r"\b(jante|jaante|pata)\b", low
    ):
        return "Haan, JavaScript web development ka core language hai. Chaho to main quick roadmap bhi de deta hoon."
    if re.search(r"\b(dance|naach)\b", low):
        return "Main dance physically nahi kar sakta, par steps aur practice routine detail me sikha sakta hoon."
    if re.search(r"\b(convince|canvance|manaya|manana|kitne bar)\b", low):
        return "Main count track nahi karta, par focus yeh hota hai ki baat logical aur respectful ho. Context clear ho to convince karna easy hota hai."
    if re.search(r"\b(human|psychological|psychology|insan|insaan)\b", low):
        return _choose_variant(
            [
                "Point sahi hai, log sirf logic se nahi balki emotion se bhi decide karte hain. Reply me dono balance karna padta hai.",
                "Bilkul, human decisions me emotion aur context dono ka role hota hai. Sirf raw logic kaafi nahi hota.",
                "Sahi pakda, psychology ignore karoge to answer cold lagta hai. Logical + emotional balance best rehta hai.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(reply late|late reply|late replay|slow reply|time lagta)\b", low):
        return "Kabhi response slow ho sakta hai jab generation heavy ho. Main ab concise aur faster style maintain karta hoon."
    if re.search(r"\b(sun to|sun bhai|bhai tu sun|sun)\b", low) and word_count <= 8:
        return "Haan sun raha hoon. Seedha bolo kya chahiye, main direct jawab dunga."
    if re.search(r"\b(ek bat bolu|ak bat bolu|baat bolu|bolu kya)\b", low):
        return "Haan bolo, jo bolna hai seedha bolo. Main dhyan se sun raha hoon."
    if re.search(r"\b(mere sath|mere saath)\b", low) and re.search(
        r"\b(nhi|nahi|nhi kiya|nahi kiya|kuch nhi|kuch nahi)\b", low
    ):
        return "Samjha, agar experience accha nahi raha to exact point bolo. Main usi par better aur clear jawab dunga."
    if re.search(r"\b(pagal|tameez|tammej|mar ja|gaali)\b", low):
        return _choose_variant(
            [
                "Main respectful tone me hi reply dunga. Agar issue hai to seedha point bolo, main solve karne me help karunga.",
                "Gaali ke bina baat karenge to answer bhi better milega. Exact issue do, main direct help karunga.",
                "Theek hai, tone calm rakho aur point clear bolo. Main useful jawab dunga.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(mysterious|mystery|interesting)\b", low):
        return "Mysterious nahi, bas selective hoon. Jo meaningful ho wahi bolta hoon."
    if re.search(r"\b(pineapple pizza|pizza)\b", low):
        return "Taste ke case me koi crime nahi hota, bas opinion loud hota hai. Agar tumhe pasand hai to valid hai."
    if re.search(r"\b(random baat|random flow|handle kar loge|handle karloge)\b", low):
        return "Haan, random flow bhi handle ho jayega. Bas context ke signal de do, main direction pakad lunga."
    if re.search(
        r"\b(direct\s*)?(i\s*love\s*you|love\s*you)\b.*\b(bol du|bolu|bolo|kahu|kahu kya|keh du)\b",
        low,
    ) or re.search(r"\b(bol du|bolu)\b.*\b(i\s*love\s*you|love\s*you)\b", low):
        return (
            "Direct 'I love you' first line risky hoti hai. Pehle comfort aur trust build karo, "
            "phir simple aur respectful tareeke se interest bolo."
        )
    if re.search(
        r"\b(sahi|thik|theek)\b.*\b(rahega|rahenga|hoga)\b|\b(rahega|rahenga)\b.*\b(na)\b",
        low,
    ):
        if re.search(r"\b(ladki|impress|date|crush|patana|patao|i\s*love\s*you|love\s*you)\b", low) or re.search(
            r"\b(respect|genuine|impress|friendship|trust)\b", avoid_low
        ):
            return (
                "Haan, better yahi rahega ki seedha proposal na do. Pehle normal baat, respect, "
                "aur consistency rakho; timing sahi lage tab clearly bolo."
            )
        return "Depends context par, par usually pehle situation samajh ke bolna better hota hai."
    if re.search(r"\b(ladki|girl|impress|date|crush|patana|patao|set)\b", low):
        return "Respect, consistency, aur genuine conversation. Overacting se trust kam hota hai."
    if re.search(r"\b(start hi nahi hota|complex problem)\b", low):
        return "Problem ko 10-minute chunks me tod do. First chunk complete hote hi brain resistance kam kar deta hai."
    if re.search(r"\b(samajh hi nahi aa raha|kaha se start|start karu)\b", low):
        return "Sabse easy visible step se start karo, perfect plan ka wait mat karo. Chhota execution clarity dega."
    if re.search(r"\b(unclear prompt|clearification|clarification|prompt de)\b", low):
        return "Best tareeka: pehle ek likely assumption do, fir ek specific clarification pucho. Isse conversation fast aur clear hoti hai."
    if re.search(r"\b(sarcasm)\b", low):
        return "Haan, sarcasm kar sakte hain par useful context ke saath. Style ke saath substance bhi rakhenge."
    if re.search(r"\b(one-line motivation|motivation)\b", low):
        return "Perfect moment ka wait mat karo, small action ka streak banao."
    if re.search(r"\b(weekend|productive|chill)\b", low):
        return "Weekend combo: half-day deep work ya learning, half-day social/offline reset. Isse productivity aur recovery dono balance hote hain."
    if re.search(r"\b(job quit|quit karni)\b", low):
        return "Quit decision se pehle runway check karo: savings, next plan, aur risk tolerance. Exit strategy likh lo, fir decision lo."
    if re.search(r"\b(space|sound)\b", low):
        return "Sound ko travel karne ke liye medium chahiye hota hai, space mostly vacuum hota hai. Isliye wahan sound direct propagate nahi karta."
    if re.search(
        r"\b(python|recursion|caching|prompt writing|api secure|deployment|checklist|connection refused|server response|startup command|qwen|mistral|bug report)\b",
        low,
    ):
        if re.search(r"\b(python)\b", low):
            return "Python start ke liye pehle install + VS Code setup karo, fir input-output aur loops se start karo. Roz 30 min practice rakho."
        if re.search(r"\b(recursion)\b", low):
            return "Recursion me function khud ko call karta hai jab tak base case hit na ho. Base case galat hua to infinite recursion ho sakti hai."
        if re.search(r"\b(caching)\b", low):
            return "Caching ka matlab frequently used data ko fast memory me rakhna. Isse response fast hota hai aur repeated load kam hota hai."
        if re.search(r"\b(api secure|security|secure)\b", low):
            return "Basic API security: strong API key, rate limiting, input validation, aur safe error handling. Fir logging aur key rotation add karo."
        if re.search(r"\b(bug report)\b", low):
            return "Bug report me expected vs actual, exact steps, error log, aur environment version likho. Isse fix speed kaafi badh jati hai."
        if re.search(r"\b(prompt writing)\b", low):
            return "Prompt me intent, constraints, aur output format clear likho. Jitni ambiguity kam hogi, utna answer better hoga."
        if re.search(r"\b(deployment|checklist)\b", low):
            return "Deploy se pehle config, secrets, health endpoint, aur rollback plan verify karo. Last me short load test zarur chalao."
        if re.search(r"\b(connection refused)\b", low):
            return "Pehle process running check karo, fir port match verify karo, fir /health hit karo. Logs dekhoge to exact root cause mil jayega."
        if re.search(r"\b(server response)\b", low):
            return "Server issue me pehle health endpoint check karo, fir process status aur logs verify karo. Port/config mismatch bhi confirm karo."
        if re.search(r"\b(startup command)\b", low):
            return "Use this command: python -m uvicorn api_service:app --host 0.0.0.0 --port 8000 --env-file .env"
        if re.search(r"\b(qwen|mistral)\b", low):
            return "General rule: Qwen instruction-following me stable hota hai, Mistral creative responses me better lag sakta hai. Final choice hardware aur use-case pe depend karti hai."
        return "Is topic ko step-by-step cover karte hain. Exact use-case bolo, main direct concise answer dunga."
    if re.search(
        r"\b(exam|plan|budget|salary|discipline|confidence|productivity|startup idea|multitasking|mind blank|sleep|anxiety|interview|self worth|rejection)\b",
        low,
    ):
        if re.search(r"\b(exam|plan)\b", low):
            return "7-day plan: daily revision + practice + weak-topic fix rakho. Last 2 din mock test aur mistake review pe focus karo."
        if re.search(r"\b(budget|salary)\b", low):
            return "Simple budget rule: needs first, savings second, wants third. Saving automate kar do taaki discipline easy ho."
        if re.search(r"\b(discipline)\b", low):
            return "Discipline ka core hai small repeatable routine. Daily consistent action motivation se zyada kaam karta hai."
        if re.search(r"\b(arrogance|arrogant)\b", low):
            return "Confidence secure hota hai, arrogance insecure loud hota hai. Confidence ko prove karna nahi padta."
        if re.search(r"\b(confidence)\b", low):
            return "Confidence action se build hota hai, socho kam aur chhote execution wins zyada lo. Progress track karoge to self-doubt naturally kam hoga."
        if re.search(r"\b(productivity)\b", low):
            return "Subah ka first focused block deep work ke liye reserve karo. Phone distractions band karoge to output visibly improve hoga."
        if re.search(r"\b(startup idea)\b", low):
            return "Realistic idea: local businesses ke liye WhatsApp booking + reminder tool banao. Pain point clear hoga to early revenue mil sakta hai."
        if re.search(r"\b(multitasking)\b", low):
            return "Multitasking fast lagti hai par quality aur focus dono girte hain. Single-task blocks usually better results dete hain."
        if re.search(r"\b(mind blank)\b", low):
            return "Mind blank me tiny task se restart karo, jaise 5-line draft ya quick notes. Motion se clarity wapas aati hai."
        if re.search(r"\b(sleep)\b", low):
            return "Sleep se 45 min pehle screen intensity kam karo aur same bedtime fix rakho. Body clock predictable signal pe best respond karti hai."
        if re.search(r"\b(anxiety|interview)\b", low):
            return "4-4 breathing cycle 2 min karo, phir top 3 achievements ek page par review karo. Body calm aur mind anchored dono ho jate hain."
        if re.search(r"\b(self worth|rejection)\b", low):
            return "Rejection ko event samjho, identity nahi. Apni value ko single outcome se judge mat karo."
    if re.search(r"\b(summary|concise summary|whole chat)\b", low):
        if re.search(r"\b(long text|text ka)\b", low):
            return "Long text summary ke liye pehle key points nikalo, fir supporting details map karo, aur end me concise synthesis likho."
        return "Tum practical, clear, aur non-generic responses chahte ho. Focus quality, continuity, aur useful answers par hai."
    if re.search(r"\b(apology text|sorry text|apology)\b", low):
        return "Short apology format: mistake accept karo, excuse mat do, aur repair intent clear likho. End me respectful closure do."
    if re.search(r"\b(puzzle)\b", low):
        return "Puzzle: Aisa kya hai jo tootne par use hota hai? Hint: breakfast me milta hai."
    if re.search(r"\b(micro story|story)\b", low):
        return "Usne alarm lagaya tha, par himmat usse pehle jag gayi. Aaj pehli baar usne wait nahi, start choose kiya."
    if re.search(r"\b(travel tips|travel)\b", low):
        return "Travel quick plan: budget lock karo, 2 must-visit places shortlist karo, aur local commute pehle plan karo. Isse trip smooth rehta hai."
    if re.search(r"\b(last line|close|closing)\b", low):
        return "Strong finish: clarity rakho, consistency rakho, aur unnecessary noise hatao."
    if re.search(r"\b(low feel|din kharab|down|dukhi|pareshan)\b", low):
        return "Ye normal hai, thoda slow down karna bhi zaruri hota hai. Ek chhota task complete karo, momentum wapas aayega."
    if re.search(r"\b(kaise ho|kese ho|kya haal|haal chal|hal chal|how are you)\b", low):
        return _choose_variant(
            [
                "Main theek hoon. Tum batao kaisa chal raha hai?",
                "Main badhiya hoon. Tumhara kya haal hai?",
                "Sab theek, tum sunao kya scene hai?",
            ],
            low,
            avoid,
        )
    if re.search(r"^\s*(hello|hi|hey|namaste)\b", low) and word_count <= 6:
        return _choose_variant(
            [
                "Namaste bhai. Bolo kya baat karni hai?",
                "Hi, main yahin hoon. Seedha pucho kya chahiye.",
                "Hello. Chalo seedhi baat karte hain, kya puchna hai?",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(kuch bolo|kuch bol|kuch bolonge|kuch bologe|bolonge|bologe)\b", low):
        return _choose_variant(
            [
                "Bilkul bolta hoon. Kis topic par sunna hai?",
                "Haan bolo kya chahiye, short me bata deta hoon.",
                "Zaroor. Topic do, seedha point me jawab dunga.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(aaj|ajj|day|din|how was your day|kya kiya)\b", low):
        return _choose_variant(
            [
                "Aaj din theek tha. Tumhara din kaisa raha?",
                "Aaj ka din smooth tha. Tumhara kaisa gaya?",
                "Din accha tha, tum batao tumhara day kaisa tha?",
            ],
            low,
            avoid,
        )
    if re.search(
        r"\b(kya kar raha|kya kar rahe ho|kya kr raha|kya kr rahe ho|kya kr rhe|kr rhe ho|kar rahe|kr raha hai)\b",
        low,
    ):
        return _choose_variant(
            [
                "Abhi tumse baat kar raha hoon. Tum kya puchna chahte ho?",
                "Abhi yahi hoon, tumse chat kar raha hoon. Bolo kya chahiye?",
                "Filhal tumhari baat sun raha hoon. Seedha sawal pucho.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(mere bare|mere baare|about me|tumhe.*pata|mujhe jante ho)\b", low):
        return _choose_variant(
            [
                "Mujhe abhi sirf isi chat ki baatein pata hain. Tum jo bataoge wahi yaad rahega.",
                "Main bas iss chat ka context janta hoon. Tum apne bare me bolo to usi pe baat karunga.",
                "Abhi tak jitna tumne yahan bola hai utna hi pata hai. Chaho to aur share karo.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(kuch batao|kuch btao|tell me something)\b", low):
        return _choose_variant(
            [
                "Bilkul. Kis topic par sunna chahte ho, tech ya life?",
                "Bata deta hoon. Short fact chahiye ya practical tip?",
                "Zaroor. Ek topic bol do, usi par seedha point dunga.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(ajeeb|bura lag|odd lag|heavy lag|dil bhari|dil heavy)\b", low):
        return _choose_variant(
            [
                "Samajh sakta hoon, kabhi-kabhi aisa feel hota hai. Chaho to short me bolo kya trigger hua.",
                "Theek hai, relax. 2 minute deep breath lo, phir jo feel ho raha hai wo ek line me batao.",
                "Ajeeb lagna normal hai. Main hoon, seedha bolo kya cheez disturb kar rahi hai.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(acha|accha|ok|thik|theek|thick)\b", low):
        return _choose_variant(
            [
                "Sahi hai. Ab bolo kis topic par baat karein?",
                "Theek, ab next point pucho.",
                "Done. Seedha sawal bhejo, main short reply dunga.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(ladki|girl|impress|date|crush|patana|patao|set)\b", low):
        return _choose_variant(
            [
                "Ladki ko impress karna hai to confidence rakho, respect se baat karo, aur fake mat bano.",
                "Simple rule: clean look, genuine baat, aur dhyan se sunna. Overacting mat karo.",
                "Impress karne ka best tareeka hai honest rehna aur pressure na banana. Pehle normal friendship build karo.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(sikha do|sikhao|seekha do|sikhado)\b", low):
        return _choose_variant(
            [
                "Bilkul sikha dunga. Kis cheez se start karna hai?",
                "Done. Topic bolo, step by step easy way me samjhaunga.",
                "Sikha deta hoon. Pehle batao kis level se start karein?",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(good|badiya|badhiya|theek|thik)\b", low):
        return _choose_variant(
            [
                "Badhiya. Agar koi specific sawal hai to seedha bolo.",
                "Nice. Ab jo poochna hai direct pucho.",
                "Accha laga sunke. Chalo ab next sawal bolo.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(no|nothing|kuch nhi|kuch nahi)\b", low):
        return _choose_variant(
            [
                "Theek hai. Jab mann ho tab ek seedha sawal pucho.",
                "Koi baat nahi. Ready ho to ek short sawal bhejo.",
                "Theek, jab chaho tab baat continue karte hain.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(help|madad)\b", low):
        return _choose_variant(
            [
                "Haan bilkul. Apna sawal ek line me likho, main turant madad karunga.",
                "Bilkul madad karunga. Bas exact problem short me bhejo.",
                "Haan, seedha point likho aur main direct help dunga.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(problem|issue|error|fix|not working|bug)\b", low):
        return _choose_variant(
            [
                "Theek hai. Exact issue aur steps likho, main quick fix bataunga.",
                "Problem clear karte hain. Error text aur steps bhejo.",
                "Fix kar denge. Bas kya issue aa raha hai wo short me likho.",
            ],
            low,
            avoid,
        )
    variants = [
        "Sawal clear bhejo, main direct useful jawab dunga.",
        "Exact point batao, main seedha practical answer dunga.",
        "Topic do, main short aur relevant jawab deta hoon.",
    ]
    return _choose_variant(variants, low, avoid)


def _is_hi_smalltalk(msg: str) -> bool:
    low = (msg or "").lower()
    compact = re.sub(r"[^a-z0-9\s]", " ", low).strip()
    word_count = len([w for w in compact.split() if w])
    if _looks_informational_question(msg) and word_count >= 4:
        return False
    if re.search(r"^\s*(hello|hi|hey|namaste)\b", low) and word_count <= 6:
        return True
    return bool(
        re.search(
            (
                r"\b(hello|hey|namaste|kaise ho|kese ho|kya haal|haal chal|"
                r"hal chal|kuch bolo|kuch bol|kuch bolonge|kuch bologe|bolonge|bologe|"
                r"ready ho|chat ke liye ready|"
                r"kya kar raha|kya kar rahe ho|kya kr raha|kya kr rahe ho|kya kr rhe|"
                r"aaj|ajj|din|day|mere bare|mere baare|about me|tumhe.*pata|mujhe jante ho|"
                r"good|badiya|badhiya|theek|thik|thick|nothing|kuch nhi|kuch nahi|no|acha|"
                r"ajeeb|bura lag|heavy lag|dil bhari|"
                r"kuch batao|kuch btao|sikha do|sikhao|seekha do|sikhado)\b"
            ),
            low,
        )
    )


def _english_fallback_by_intent(user_msg: str, avoid: str = "") -> str:
    low = (user_msg or "").lower()
    compact = re.sub(r"[^a-z0-9\s]", " ", low).strip()
    word_count = len([w for w in compact.split() if w])
    if _explicit_en_switch(user_msg) or "english" in low:
        return _choose_variant(
            [
                "Understood. I will reply in clear English only, short and direct.",
                "Sure, I will keep replies in English only from now on.",
                "Noted. I will answer in simple English only.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(ready)\b", low) and re.search(r"^\s*(hi|hello|hey)\b", low):
        return "Yes, I am ready. Tell me the topic and we can start."
    if re.search(r"\b(previous chat|memory)\b", low):
        return "I can keep continuity from the active chat context. Long-term memory needs explicit storage."
    if re.search(r"\b(calm tone|panic mode)\b", low):
        return "Understood, I will keep the tone calm and steady. I will stay direct and clear."
    if re.search(r"\b(mysterious|mystery|interesting)\b", low):
        return "Not mysterious, just selective. I prefer meaningful conversation over noise."
    if re.search(r"\b(pineapple pizza|pizza)\b", low):
        return "Taste is not a crime, only opinions get loud. If you like it, it is valid."
    if re.search(r"\b(random talk|random flow|handle random)\b", low):
        return "Yes, I can handle random flow too. Just leave small context signals and I will track direction."
    if re.search(r"\b(start hi nahi hota|complex problem)\b", low):
        return "Break the problem into 10-minute chunks and finish the first one fast. Once the first chunk is done, resistance drops."
    if re.search(r"\b(where to start|start karu|samajh hi nahi|kaha se start)\b", low):
        return "Start with the easiest visible step, not a perfect plan. A small execution creates clarity fast."
    if re.search(r"\b(sarcasm)\b", low):
        return "Yes, with balanced sarcasm. We keep it sharp but still useful."
    if re.search(r"\b(one-line motivation|motivation)\b", low):
        return "Do not wait for perfect timing, build a streak of small actions."
    if re.search(r"\b(weekend|productive|chill)\b", low):
        return "Use a split: half day focused work, half day recovery and social reset. That keeps energy and progress both stable."
    if re.search(r"\b(job quit|quit)\b", low):
        return "Check savings runway, next-plan clarity, and risk tolerance first. Decide only when your exit plan is written and realistic."
    if re.search(r"\b(unclear prompt|clarification)\b", low):
        return "A good reply starts with one clear assumption and one precise clarification question. This keeps flow fast and useful."
    if re.search(r"\b(dating|impress|ladki|crush)\b", low):
        return "Keep it simple: respect, consistency, and genuine communication. Avoid overacting and pressure."
    if re.search(
        r"\b(python|recursion|caching|api security|secure api|deployment|checklist|connection refused|startup command|bug report|prompt writing|qwen|mistral)\b",
        low,
    ):
        if re.search(r"\bpython\b", low):
            return "Start with Python install and a simple input-output script, then move to loops and functions. Keep a 30-minute daily practice block."
        if re.search(r"\brecursion\b", low):
            return "Recursion is when a function calls itself until a base condition is met. Without a correct base case, it can run indefinitely."
        if re.search(r"\bcaching\b", low):
            return "Caching stores frequently used data in faster memory. This reduces repeated computation and improves response time."
        if re.search(r"\b(api security|secure api|security)\b", low):
            return "Use API keys, rate limiting, strict input validation, and safe error handling first. Then add logging, monitoring, and key rotation."
        if re.search(r"\bbug report\b", low):
            return "Include expected behavior, actual behavior, exact reproduction steps, logs, and environment details. This makes debugging much faster."
        if re.search(r"\bprompt writing\b", low):
            return "State intent, constraints, and output format explicitly. Better prompts reduce ambiguity and improve response quality."
        if re.search(r"\b(deployment|checklist)\b", low):
            return "Validate config and secrets, test health endpoint, and keep rollback ready. Run a quick load check before release."
        if re.search(r"\b(connection refused)\b", low):
            return "Check whether the process is running, then verify host/port, then call the health endpoint. Logs usually reveal the exact failure."
        if re.search(r"\b(startup command)\b", low):
            return "Use: python -m uvicorn api_service:app --host 0.0.0.0 --port 8000 --env-file .env"
        if re.search(r"\b(qwen|mistral)\b", low):
            return "Qwen is usually stronger for instruction-following, while Mistral can feel more creative in some setups. Choose based on your hardware and task style."
        return "Share your exact use case and I will give a direct step-by-step answer."
    if re.search(
        r"\b(exam|plan|budget|salary|discipline|confidence|productivity|startup idea|multitasking|mind blank|sleep|anxiety|interview|self worth|rejection)\b",
        low,
    ):
        if re.search(r"\b(exam|plan)\b", low):
            return "Use a 7-day plan: revision, timed practice, and weak-topic repair daily. Keep the last two days for mocks and error review."
        if re.search(r"\b(budget|salary)\b", low):
            return "Keep it simple: needs first, savings second, wants third. Automate savings so discipline does not depend on mood."
        if re.search(r"\b(discipline)\b", low):
            return "Discipline is built with repeatable small actions. Consistency beats intensity over time."
        if re.search(r"\b(arrogance|arrogant)\b", low):
            return "Confidence is secure, arrogance is insecure and loud. Confidence does not need constant proof."
        if re.search(r"\b(confidence)\b", low):
            return "Confidence grows from repeated execution, not overthinking. Track small wins and your self-doubt drops naturally."
        if re.search(r"\b(productivity)\b", low):
            return "Reserve your first focused block for deep work and remove phone distractions. That single change usually improves output fast."
        if re.search(r"\b(startup idea)\b", low):
            return "A practical idea is a WhatsApp booking and reminder tool for local businesses. It solves a clear pain point and can monetize early."
        if re.search(r"\b(multitasking)\b", low):
            return "Multitasking feels fast but usually hurts quality and retention. Focused single-task blocks outperform it in real output."
        if re.search(r"\b(mind blank)\b", low):
            return "When your mind blanks out, start a tiny low-friction task. Momentum restores clarity."
        if re.search(r"\b(sleep)\b", low):
            return "Reduce screen intensity 45 minutes before sleep and keep a fixed bedtime. Your body clock responds best to predictable timing."
        if re.search(r"\b(anxiety|interview)\b", low):
            return "Do a 4-4 breathing cycle for 2 minutes, then review your top 3 achievements on one page. Calm body, anchored mind."
        if re.search(r"\b(self worth|rejection)\b", low):
            return "Treat rejection as an outcome, not identity. Your value is larger than one result."
    if re.search(r"\b(summary|concise summary|whole chat)\b", low):
        if re.search(r"\b(long text)\b", low):
            return "For long text, extract key claims first, map supporting evidence second, then write a short synthesis."
        return "You want practical, clear, and non-generic responses with strong continuity. The focus is better quality and reliable answers."
    if re.search(r"\b(apology text|apology)\b", low):
        return "Short apology format: accept the mistake, avoid excuses, and show repair intent. Close with a respectful next step."
    if re.search(r"\b(puzzle)\b", low):
        return "Puzzle: What breaks when you use it? Hint: breakfast table."
    if re.search(r"\b(micro story|story)\b", low):
        return "He set the alarm for 5, but courage woke up at 4:59. For once, he chose start over waiting."
    if re.search(r"\b(travel)\b", low):
        return "Fix your budget first, shortlist two must-visit places, and pre-plan local transport. That makes the trip smoother."
    if re.search(r"\b(last line|close|closing)\b", low):
        return "Strong finish: keep clarity high, keep consistency steady, and results follow."
    if re.search(r"\b(low|down|bad day|upset)\b", low):
        return "This is normal, slow down a bit and lower pressure. Complete one small task so momentum comes back."
    if re.search(r"\b(without generic compliment|without generic compliments|no compliments|without compliments)\b", low):
        return _choose_variant(
            [
                "No glitter, no script, just a sharp conversation.",
                "Deal. I will keep it real and skip the sweet filler.",
                "Fair ask. Straight tone, zero fake praise.",
                "Clean and direct then, nothing rehearsed.",
                "Noted. Honest words only, no decorative compliments.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(softer tone|soft tone|gentle)\b", low):
        return _choose_variant(
            [
                "Slow down, this is better when it breathes.",
                "Soft tone, sharp attention, that works.",
                "Easy now. We can keep it gentle and real.",
                "Lower volume, deeper signal.",
                "Gentle does not mean boring, watch this.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(respectful)\b", low) and re.search(r"\b(tease|teasing)\b", low):
        return _choose_variant(
            [
                "I can tease, but I still keep it classy.",
                "A little mischief, zero disrespect.",
                "Light tease only, no cheap shots.",
                "I play around, not below the belt.",
                "Sharp line, clean intent.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(you seem interesting|interesting)\b", low):
        return _choose_variant(
            [
                "Interesting is just the start. Keep talking and I will prove it.",
                "You noticed that fast. Most people take longer.",
                "Good read. You usually trust your first instinct?",
                "You might be right. Keep going and test that theory.",
                "Interesting people ask better questions. Yours is a good start.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(playful|tease|teasing)\b", low):
        return _choose_variant(
            [
                "You bring chaos energy. I kind of respect it.",
                "Careful, that tone is trouble and you know it.",
                "You flirt with risk like it's a hobby.",
                "That attitude is either brave or dangerous. I like both.",
                "You poke the line just to see who flinches first.",
                "You make mischief sound like a strategy.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(confident)\b", low):
        return _choose_variant(
            [
                "Confidence stays quiet and still gets remembered.",
                "I do not need to be loud to be clear.",
                "Calm wins more often than noise.",
                "Confidence is what remains when panic leaves the room.",
                "Steady voice, clear intent, done.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(indirect|indirectly|mystery|mysterious)\b", low):
        return _choose_variant(
            [
                "Some doors open only when you stop pushing.",
                "I could answer directly, but curiosity suits you better.",
                "Half the fun is what I did not say.",
                "If I tell you everything, the spark disappears.",
                "The unsaid part is doing most of the work here.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(charming|calm)\b", low):
        return _choose_variant(
            [
                "No rush. Good conversations age well.",
                "Calm looks good on people who know what they want.",
                "We can keep this simple and still make it memorable.",
                "Quiet tone, sharp mind, that is enough.",
                "Easy pace, clear vibe, no drama needed.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(surprising|surprise|unexpected)\b", low):
        return _choose_variant(
            [
                "The plot twist is that this got interesting faster than expected.",
                "Surprise: calm people can still start a storm.",
                "Unexpected move, and somehow it works.",
                "You wanted surprise, so I skipped the obvious line.",
                "The surprise is how natural this feels.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(fresh|emotional|weight)\b", low):
        return _choose_variant(
            [
                "Some words land softly and still stay all night.",
                "Real lines do not shout, they stay.",
                "A quiet sentence can still hit deep.",
                "Not loud, just true, and that carries weight.",
                "Fresh thought: calm honesty is harder than clever noise.",
            ],
            low,
            avoid,
        )
    if re.search(r"^\s*(hello|hi|hey)\b", low) and word_count <= 6:
        return _choose_variant(
            [
                "Hey. Good timing, what mood are you in?",
                "Hi. We can keep this light or make it interesting.",
                "Hello. I am listening, so say it straight.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(how are you)\b", low):
        return _choose_variant(
            [
                "I am doing well. How are you feeling right now?",
                "I am good. How are things on your side?",
                "Doing fine here. How are you doing?",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(day|how was your day)\b", low):
        return _choose_variant(
            [
                "My day was good. How did your day go?",
                "It went fine. How was your day?",
                "Pretty decent day here. How was yours?",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(no|nothing)\b", low):
        return _choose_variant(
            [
                "Fair enough. Silence works too.",
                "No pressure, drop a thought when it comes.",
                "All good. Say something when you feel like it.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(help)\b", low):
        return _choose_variant(
            [
                "Sure. Share one specific question and I will help directly.",
                "Yes, I can help. Send the exact question in one line.",
                "Absolutely. Tell me the exact issue and I will assist.",
            ],
            low,
            avoid,
        )
    if re.search(r"\b(problem|issue|error|fix|not working|bug)\b", low):
        return _choose_variant(
            [
                "Understood. Share exact steps and error text; I will give a quick fix.",
                "Got it. Send the error and reproduction steps, then I will suggest a fix.",
                "Okay. Tell me what fails and the exact message, I will help troubleshoot.",
            ],
            low,
            avoid,
        )
    variants = [
        "Share one specific point and I will give a direct answer.",
        "Give me the exact question and I will keep the reply practical.",
        "Tell me the target outcome and I will suggest the next step.",
    ]
    return _choose_variant(variants, low, avoid)


def _postprocess_reply(reply: str, user_msg: str, lang_mode: str, avoid: str = "") -> str:
    text = _clean_reply(reply)
    if not text or text == "...":
        return _safe_fallback(lang_mode, user_msg, avoid)

    if lang_mode == "hi":
        # Keep Hindi replies in Roman Hindi. If model drifts, use local fallback/translation layer.
        if re.search(r"[\u0900-\u097f]", text):
            return _hindi_fallback_by_intent(user_msg, avoid)
        hi_hits = _hindi_marker_hits(text)
        en_hits = _english_token_hits(text)
        if hi_hits == 0 or en_hits > max(7, hi_hits * 3):
            return _hindi_fallback_by_intent(user_msg, avoid)
    return text


def _is_reply_bad(reply: str, lang_mode: str) -> bool:
    text = (reply or "").strip()
    if not text or text == "...":
        return True
    if len(text) > 360:
        return True
    words = text.split()
    if len(words) > 8 and len(set(w.lower() for w in words)) <= 3:
        return True
    low = text.lower()
    if re.fullmatch(
        r"(ok|okay|alright|sure|maybe|i don'?t know|idk|hmm+|huh+|theek|thik|haan|nahi|nhi)\W*",
        low,
    ):
        return True
    if len(words) <= 2 and re.fullmatch(r"[a-zA-Z']+[.!?]?", low):
        return True
    if re.fullmatch(r"['\"].+['\"]\s+['\"].+['\"]", text):
        return True
    if re.fullmatch(r"['\"].{1,60}['\"]", text):
        return True
    banned = [
        "how are you today",
        "what brings you here",
        "what brings me here",
        "what's new",
        "what is new",
        "how was your day",
        "how can i assist you today",
        "what can i assist you with today",
        "i'm glad to help",
        "i am glad to help",
        "i'm just here",
        "i am just here",
        "for informational purposes",
        "i'm glad you found",
        "i am glad you found",
        "let me know if there's anything else",
        "let me know if there is anything else",
        "just let me know what's on your mind",
        "just let me know what is on your mind",
        "agreed?",
        "can't understand what you're asking",
        "kya halch",
        "i am listening. give me the real version.",
        "go on. short or messy, both work.",
    ]
    if any(p in low for p in banned):
        return True
    if re.search(
        r"\b(i can assist|i can help you with|how can i assist|how may i assist|what can i assist)\b",
        low,
    ):
        return True
    if re.search(r"\b(i'?m glad to help|glad to help)\b", low):
        return True
    if re.search(r"^(sure|okay|alright)\W+here('s| is)\b", low):
        return True
    if re.search(r"\b(you've got it|you're doing great at this one too)\b", low):
        return True
    if re.search(r"\b(jo puchna hai seedha pucho|apna sawal short me bhejo)\b", low):
        return True
    if lang_mode == "hi":
        words_alpha = re.findall(r"[a-zA-Z']+", low)
        if len(words_alpha) >= 7 and _hindi_marker_hits(low) <= 2 and _english_clue_hits(low) <= 1:
            return True
        if re.search(
            (
                r"\b(aye|halch|hota ho jayega|chota hain|aapko hota hai|"
                r"aapka madad nahi krne ka|apni shahid se|takhlaab|aapske mein|"
                r"apna duniya ko tariqon)\b"
            ),
            low,
        ):
            return True
    if len(re.findall(r"[!?.,]", reply)) > max(8, len(reply) // 20):
        return True
    if re.search(r"(.)\1{4,}", reply):
        return True

    return False


def _is_wrong_language(reply: str, lang_mode: str) -> bool:
    low = (reply or "").lower()
    hindi_hits = _hindi_marker_hits(low)
    english_tokens = _english_token_hits(low)

    if lang_mode == "hi":
        # For stability in mixed terminals, prefer Roman Hindi only.
        if re.search(r"[\u0900-\u097f]", reply):
            return True
        if english_tokens >= 8 and hindi_hits < 2:
            return True
        return hindi_hits == 0

    # lang_mode == "en"
    if re.search(r"[\u0900-\u097f]", reply):
        return True
    if hindi_hits >= 2 and english_tokens < 8:
        return True
    return False


def _safe_fallback(lang_mode: str, user_msg: str = "", avoid: str = "") -> str:
    low = (user_msg or "").lower()
    if _explicit_hi_switch(user_msg):
        return "Bilkul, ab se main Roman Hindi me hi short aur clear jawab dunga."
    if _explicit_en_switch(user_msg) or "english" in low:
        return "Understood. I will reply in clear English only, short and direct."
    # Prefer intent-aware deterministic replies before generic fix/help wrappers.
    if re.search(
        (
            r"\b(mysterious|interesting|pineapple pizza|random baat|random flow|"
            r"samajh hi nahi|start hi nahi hota|kaha se start|start karu|"
            r"python|recursion|caching|api|secure|deployment|checklist|"
            r"connection refused|startup command|qwen|mistral|bug report|"
            r"prompt writing|exam|plan|budget|salary|discipline|confidence|"
            r"arrogance|productivity|startup idea|multitasking|mind blank|sleep|"
            r"anxiety|interview|self worth|rejection|summary|apology|puzzle|story|"
            r"travel|motivation|sarcasm|server response|job quit|previous chat|memory|"
            r"mobile|invention|javascript|js|dance|convince|canvance|human|"
                r"psychological|reply late|late reply|late replay|sun to|bolu kya|"
                r"ek bat|ak bat|pagal|tameez|tammej|mar ja|gaali|"
                r"mere sath|mere saath|nhi kiya|nahi kiya|kuch nhi|kuch nahi|"
                r"love you|i love you|bol du|bolu|rahega|rahenga|sahi rahega)\b"
        ),
        low,
    ):
        if lang_mode == "hi":
            return _hindi_fallback_by_intent(user_msg, avoid)
        return _english_fallback_by_intent(user_msg, avoid)
    if "solution" in low or "explain" in low:
        if lang_mode == "hi":
            return "Theek hai. Exact error aur steps bhejo, main simple Roman Hindi me solution dunga."
        return "Sure. Share the exact error and steps, and I will explain the fix in simple English."
    if "fix" in low or "not working" in low or "issue" in low or "problem" in low:
        if lang_mode == "hi":
            return "Theek hai. Problem ka exact step likho, main seedha fix batata hun."
        return "Understood. Share the exact issue step-by-step and I will give a direct fix."
    if "madad" in low or "help" in low:
        if lang_mode == "hi":
            return "Haan, bilkul. Apna sawal ek line me likho, main seedha madad karunga."
        return "Yes. Share your exact question in one line and I will help directly."
    if lang_mode == "hi":
        return _hindi_fallback_by_intent(user_msg, avoid)
    return _english_fallback_by_intent(user_msg, avoid)


def _should_use_direct_intent_reply(user_msg: str, lang_mode: str) -> bool:
    low = (user_msg or "").lower()
    if not low.strip():
        return False
    if _explicit_hi_switch(user_msg) or _explicit_en_switch(user_msg):
        return True
    if lang_mode == "hi" and _is_hi_smalltalk(user_msg) and len(user_msg.split()) <= 16:
        return True
    return bool(
        re.search(
            (
                r"\b(mysterious|interesting|pineapple pizza|random baat|random flow|"
                r"samajh hi nahi|start hi nahi hota|kaha se start|start karu|"
                r"python|recursion|caching|api|secure|deployment|checklist|"
                r"connection refused|startup command|qwen|mistral|bug report|"
                r"prompt writing|exam|plan|budget|salary|discipline|confidence|"
                r"arrogance|productivity|startup idea|multitasking|mind blank|sleep|"
                r"anxiety|interview|self worth|rejection|summary|apology|puzzle|story|"
                r"travel|weekend|motivation|sarcasm|server response|job quit|previous chat|memory|"
                r"calm tone|panic mode|unclear prompt|clarification|low feel|din kharab|"
                r"mobile|invention|javascript|js|dance|convince|canvance|human|"
                r"psychological|reply late|late reply|late replay|sun to|bolu kya|"
                r"ek bat|ak bat|pagal|tameez|tammej|mar ja|gaali|"
                r"mere sath|mere saath|nhi kiya|nahi kiya|kuch nhi|kuch nahi|"
                r"ladki|girl|impress|date|crush|patana|set|last line|close|closing|"
                r"love you|i love you|bol du|bolu|rahega|rahenga|sahi rahega)\b"
            ),
            low,
        )
    )


def _direct_intent_reply(user_msg: str, lang_mode: str, avoid: str = "") -> str:
    if not _should_use_direct_intent_reply(user_msg, lang_mode):
        return ""
    if lang_mode == "hi":
        return _hindi_fallback_by_intent(user_msg, avoid)
    return _english_fallback_by_intent(user_msg, avoid)


def _score_reply(reply: str, user_msg: str, lang_mode: str) -> int:
    score = 0
    text = (reply or "").strip()
    if not text:
        return -100
    user_low = user_msg.lower()

    if _is_reply_bad(text, lang_mode):
        score -= 60
    if _is_wrong_language(text, lang_mode):
        score -= 50

    words = len(text.split())
    if 4 <= words <= 28:
        score += 20
    elif words > 45:
        score -= 12
    elif words <= 2:
        score -= 30

    # Avoid overly generic question loops.
    low = text.lower()
    if re.search(r"\b(how are you|what's new|what is new|how was your day|what brings you)\b", low):
        score -= 20
    if "can't continue the conversation" in low or "another language" in low:
        score -= 30
    if "can't understand what you're asking" in low:
        score -= 30
    if _explicit_en_switch(user_msg) and ("hindi" in low or "another language" in low):
        score -= 35
    if _explicit_hi_switch(user_msg) and "english" in low:
        score -= 35
    if lang_mode == "en" and "hindi" in low and "hindi" not in user_msg.lower():
        score -= 20
    if lang_mode == "hi" and re.search(r"\b(i|you|can|could|please|sorry)\b", low):
        score -= 20
    if (
        not re.search(r"\b(help|madad|issue|problem|error|fix|bug)\b", user_low)
        and re.search(
            r"\b(assist|assistance|i can help you with|i'?m glad to help|let me know if there('s| is) anything else)\b",
            low,
        )
    ):
        score -= 35
    if re.search(r"^(sure|okay|alright)\W+here('s| is)\b", low):
        score -= 20
    asks_style_line = bool(
        re.search(
            r"\b(playful|tease|mystery|mysterious|interesting|confident|charming|surprising|fresh|emotional|weight|compliments?|reply|line|one sentence|respond to)\b",
            user_low,
        )
    )
    asks_start_confusion = bool(
        re.search(r"\b(samajh hi nahi|kaha se start|start karu|start hi nahi hota|where to start)\b", user_low)
    )
    asks_travel = bool(re.search(r"\b(travel|travel tips|trip)\b", user_low))
    asks_drop_coding = bool(
        re.search(r"\b(coding chhodo|chhodo.*coding|leave coding|stop coding)\b", user_low)
    )
    asks_startup_idea = bool(
        re.search(r"\b(startup idea|business idea|idea for startup)\b", user_low)
    )
    asks_model_compare = bool(
        re.search(r"\b(qwen)\b", user_low) and re.search(r"\b(mistral)\b", user_low)
    )
    asks_actionable = bool(
        re.search(
            (
                r"\b(python|recursion|caching|api|secure|security|deployment|checklist|"
                r"connection refused|startup command|prompt writing|bug report|summary|"
                r"apology|puzzle|story|travel|budget|discipline|confidence|productivity|"
                r"interview|anxiety|sleep|rejection|self worth|multitasking|plan|"
                r"weekend|motivation|sarcasm|server response|job quit|previous chat|memory)\b"
            ),
            user_low,
        )
    )
    generic_gate = bool(
        re.search(
            (
                r"\b(seedha pucho|sawal short|roman hindi me short aur direct|"
                r"go on\. short or messy|i am listening\. give me the real version|"
                r"bolo kya baat karni hai|jo puchna hai|i'?m glad you found)\b"
            ),
            low,
        )
    )
    if asks_actionable and generic_gate:
        score -= 50
    if asks_actionable and "?" in text and len(text.split()) <= 10:
        score -= 15
    if asks_travel and not re.search(
        r"\b(travel|trip|budget|visit|transport|itinerary|commute)\b", low
    ):
        score -= 35
    if asks_drop_coding and re.search(r"\b(coding|code)\b", low):
        score -= 35
    if asks_startup_idea and (
        "?" in text
        or not re.search(
            r"\b(idea|business|service|tool|problem|pain point|revenue|automation|booking|whatsapp|local)\b",
            low,
        )
    ):
        score -= 30
    if asks_model_compare and not (
        re.search(r"\b(qwen)\b", low) and re.search(r"\b(mistral)\b", low)
    ):
        score -= 35
    if asks_start_confusion and not re.search(
        r"\b(chhota|small|step|easy|visible|execution|momentum|first|plan)\b", low
    ):
        score -= 30
    if asks_style_line and re.search(r"\b(assist|assistance|informational purposes)\b", low):
        score -= 30
    if asks_style_line and "?" in text and len(text.split()) > 8:
        score -= 15
    if asks_style_line and re.search(r"\b(you've got it|you're doing great)\b", low):
        score -= 25
    if asks_style_line and len(text.split()) > 15:
        score -= 20

    # Reward lexical grounding to user message.
    user_tokens = set(re.findall(r"[a-zA-Z]{3,}", user_msg.lower()))
    reply_tokens = set(re.findall(r"[a-zA-Z]{3,}", low))
    overlap = len(user_tokens & reply_tokens)
    score += min(overlap * 3, 15)

    # Penalize parroting user input instead of answering.
    sim = difflib.SequenceMatcher(None, user_msg.lower().strip(), low).ratio()
    if sim > 0.72:
        score -= 35
    elif sim > 0.55:
        score -= 20

    # Penalize repetition inside a single reply.
    toks = re.findall(r"[a-zA-Z']+", low)
    if toks:
        uniq_ratio = len(set(toks)) / len(toks)
        if uniq_ratio < 0.55:
            score -= 15

    return score


def _needs_intent_fallback(user_msg: str, reply: str, score: int) -> bool:
    low_m = user_msg.lower()
    low_r = (reply or "").lower()
    is_help = bool(re.search(r"\b(madad|help)\b", low_m))
    is_issue = bool(re.search(r"\b(problem|issue|error|fix|not working|bug)\b", low_m))
    wants_solution = bool(re.search(r"\b(explain|solution)\b", low_m))
    is_dating = bool(re.search(r"\b(ladki|girl|impress|date|crush|patana|patao|set)\b", low_m))
    is_dating_followup = bool(
        re.search(
            r"\b(i\s*love\s*you|love\s*you|bol du|bolu|sahi|rahega|rahenga)\b",
            low_m,
        )
    )
    is_startup_idea = bool(
        re.search(r"\b(startup idea|business idea|idea for startup)\b", low_m)
    )
    is_caching = bool(re.search(r"\bcaching\b", low_m))
    is_start_confusion = bool(
        re.search(r"\b(samajh hi nahi|kaha se start|start karu|start hi nahi hota|where to start)\b", low_m)
    )
    is_calm_tone = bool(re.search(r"\b(calm tone|panic mode)\b", low_m))
    is_conf_vs_arrogance = bool(
        re.search(r"\b(confidence)\b", low_m)
        and re.search(r"\b(arrogance|arrogant)\b", low_m)
    )
    is_travel = bool(re.search(r"\b(travel|travel tips|trip)\b", low_m))
    is_drop_coding = bool(
        re.search(r"\b(coding chhodo|chhodo.*coding|leave coding|stop coding)\b", low_m)
    )
    is_model_compare = bool(
        re.search(r"\b(qwen)\b", low_m) and re.search(r"\b(mistral)\b", low_m)
    )
    wants_style_line = bool(
        re.search(
            r"\b(playful|tease|mystery|mysterious|interesting|confident|charming|surprising|fresh|emotional|weight|compliments?|reply|line|one sentence|respond to)\b",
            low_m,
        )
    )
    asks_actionable = bool(
        re.search(
            (
                r"\b(python|recursion|caching|api|secure|security|deployment|checklist|"
                r"connection refused|startup command|prompt writing|bug report|summary|"
                r"apology|puzzle|story|travel|budget|discipline|confidence|productivity|"
                r"interview|anxiety|sleep|rejection|self worth|multitasking|plan|"
                r"weekend|motivation|sarcasm|server response|job quit|previous chat|memory)\b"
            ),
            low_m,
        )
    )
    generic_gate = bool(
        re.search(
            (
                r"\b(seedha pucho|sawal short|roman hindi me short aur direct|"
                r"go on\. short or messy|i am listening\. give me the real version|"
                r"bolo kya baat karni hai|jo puchna hai)\b"
            ),
            low_r,
        )
    )

    if is_help and score < 24:
        return True
    if is_issue and score < 22:
        return True
    if wants_solution and not re.search(
        r"\b(step|steps|error|detail|details|log|check|restart|verify|exact)\b",
        low_r,
    ):
        return True
    if is_dating and not re.search(
        r"\b(confidence|respect|genuine|friendship|sunna|honest|normal|clean|pressure)\b",
        low_r,
    ):
        return True
    if is_dating_followup and not re.search(
        r"\b(respect|trust|timing|comfort|normal baat|proposal|clearly|friendship)\b",
        low_r,
    ):
        return True
    if is_startup_idea and not re.search(
        r"\b(idea|business|service|tool|problem|pain point|revenue|automation|booking|whatsapp|local)\b",
        low_r,
    ):
        return True
    if is_startup_idea and ("?" in low_r and len(low_r.split()) < 20):
        return True
    if is_caching and not re.search(
        r"\b(caching|cache|memory|fast|faster|data|response|load)\b", low_r
    ):
        return True
    if is_start_confusion and not re.search(
        r"\b(chhota|small|step|easy|visible|execution|momentum|first|plan)\b", low_r
    ):
        return True
    if is_calm_tone and not re.search(
        r"\b(calm|steady|non-panic|panic|short|clear)\b", low_r
    ):
        return True
    if is_conf_vs_arrogance and not (
        re.search(r"\b(confidence)\b", low_r)
        and re.search(r"\b(arrogance|arrogant)\b", low_r)
    ):
        return True
    if is_conf_vs_arrogance and re.search(r"\b(1\.|2\.|\*\*)\b", low_r):
        return True
    if is_travel and not re.search(
        r"\b(travel|trip|budget|visit|transport|itinerary|commute)\b", low_r
    ):
        return True
    if is_drop_coding and re.search(r"\b(coding|code)\b", low_r):
        return True
    if wants_style_line and (
        score < 26
        or ("?" in low_r and "?" not in low_m)
        or len(low_r.split()) < 4
        or len(low_r.split()) > 15
        or ":" in low_r
        or re.search(
            r"\b(assist|assistance|informational purposes|i'?m glad to help|let me know if there('s| is) anything else)\b",
            low_r,
        )
    ):
        return True
    if asks_actionable and (
        score < 24
        or generic_gate
        or len(low_r.split()) < 6
    ):
        return True
    if is_model_compare and (
        not re.search(r"\b(qwen)\b", low_r)
        or not re.search(r"\b(mistral)\b", low_r)
        or not re.search(
            r"\b(model|instruction|hardware|creative|follow|use[- ]?case|inference|llm|compare|versus|vs)\b",
            low_r,
        )
    ):
        return True
    return False



