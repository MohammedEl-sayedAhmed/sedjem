"""Made-up meeting lines for the README screenshots: an Egyptian Arabic-English sprint planning."""

# (speaker, seconds long, text)
RAW = [
    ("1", 4.6, "صباح الخير يا جماعة، خلونا نبدأ ال sprint planning بسرعة عشان عندنا demo الساعة اتنين"),
    ("2", 5.2, "تمام، أنا بس عايز أفكركم إن ال release بتاعة الأسبوع اللي فات لسه فيها bug في ال login"),
    ("1", 3.4, "The login bug is on the board already, صح يا كريم؟"),
    ("2", 4.8, "أيوه، عملتله ticket وحطيته high priority، المشكلة في ال session timeout"),
    ("3", 5.6, "أنا شفت ال logs امبارح، ال token بيخلص قبل ما ال refresh يحصل، فال user بيطلع برا"),
    ("1", 3.0, "طب مين هياخده الـ sprint دي؟"),
    ("3", 4.2, "I can take it. هو محتاج يومين تقريباً مع ال testing"),
    ("1", 4.4, "حلو. تاني حاجة، ال dashboard الجديد، الـ design خلص ولا لسه؟"),
    ("4", 6.0, "خلص تقريباً، فاضل بس ال empty states وال dark mode، هبعتلكم ال Figma link النهارده"),
    ("2", 4.6, "Please make sure the charts work on mobile, المرة اللي فاتت كانت مكسورة على الشاشات الصغيرة"),
    ("4", 3.8, "أكيد، أنا عاملة breakpoints جديدة للموبايل والتابلت"),
    ("1", 5.0, "طيب نيجي لل estimation. ال dashboard كام story point في رأيكم؟"),
    ("3", 3.2, "أنا شايف إنه eight، فيه API جديدة كمان"),
    ("2", 4.4, "Eight is fair, بس لو ال API مش جاهزة هنتأخر، هي على ال back-end team"),
    ("1", 4.0, "هكلم أحمد من ال back-end النهارده وأتأكد من ال timeline"),
    ("4", 5.4, "وممكن نبدأ بـ mock data لحد ما ال endpoint يبقى جاهز، كده مش هنستنى"),
    ("1", 3.6, "Good idea. نعمل كده ونبدل لما ال API تنزل على staging"),
    ("3", 5.8, "في حاجة كمان، ال CI pipeline بقت بطيئة جداً، ال build بياخد تقريباً عشرين دقيقة"),
    ("2", 3.4, "عشرين دقيقة؟ ده كتير أوي، كان سبعة بس الشهر اللي فات"),
    ("3", 5.0, "I think it's the new integration tests. بيشغلوا الـ database من الأول في كل test"),
    ("1", 4.2, "ممكن نعمل cache لل dependencies ونقسم ال tests على أكتر من runner؟"),
    ("3", 3.6, "ممكن، هجرب parallel jobs وأقولكم بكره"),
    ("4", 4.8, "وأنا محتاجة حد يعمل review لل merge request بتاع ال onboarding screens"),
    ("2", 3.2, "ابعتيهولي، هبصله بعد ال standup"),
    ("1", 5.4, "تمام. يعني ال sprint دي فيها ال login bug، ال dashboard، وال pipeline. في حاجة ناقصة؟"),
    ("2", 4.6, "The customer feedback from last week، فيه تلات requests عن ال export to Excel"),
    ("1", 4.4, "دي نحطها في ال backlog ونشوفها في ال sprint الجاية، مش هنلحق"),
    ("4", 3.0, "موافقة، خلونا نركز على ال dashboard الأول"),
    ("1", 4.8, "خلاص، ال sprint goal إننا نسلم ال dashboard على staging يوم الخميس"),
    ("3", 2.8, "Sounds good. هحدث ال board دلوقتي"),
    ("1", 4.2, "شكراً يا جماعة، نتقابل في ال demo الساعة اتنين"),
]

TITLE = "Sprint planning"
NAMES = {"1": "Mona", "2": "Karim", "3": "Omar", "4": "Nour"}


def lines(raw=RAW, start=0.4, gap=0.3):
    out, t = [], start
    for spk, dur, text in raw:
        out.append({"start": round(t, 2), "end": round(t + dur, 2), "speaker": spk, "text": text})
        t += dur + gap
    return out


# The second model (whisper-medium) on the same recording: a little later, its own speaker numbers, and the
# differences the project measured for it: English terms kept in English (90% against Cohere's 77% on Perle),
# but more errors on Arabic words (17.8% against 5.1%): Egyptian turned formal, a word misheard, a gender slip.
# Where the Cohere run wrote an English term in Arabic letters (ايت, الباك اند, كاش), whisper-medium has it right.
SPK2 = {"1": "2", "2": "1", "3": "3", "4": "4"}
CHANGES = {
    1: "تمام، أنا فقط أريد أن أذكركم إن ال release بتاعة الأسبوع اللي فات لسه فيها bug في ال login",
    4: "أنا شفت ال logs امبارح، ال token بينتهي قبل ما ال refresh يحصل، فال user بيطلع برا",
    8: "خلص تقريباً، فاضل بس ال empty states وال dark mode، سأرسل لكم ال Figma link اليوم",
    10: "أكيد، أنا عامل breakpoints جديدة للموبايل والتابلت",
    17: "في حاجة كمان، ال CI pipeline أصبحت بطيئة جداً، ال build بياخد تقريباً عشرين دقيقة",
    26: "هذه نضعها في ال backlog ونشوفها في ال sprint الجاية، مش هنلحق",
}


def second():
    out = []
    for i, x in enumerate(lines()):
        out.append(dict(x, start=round(x["start"] + 0.12, 2), end=round(x["end"] + 0.08, 2),
                        speaker=SPK2[x["speaker"]], text=CHANGES.get(i, x["text"])))
    return out


# Short transcripts for the other recordings in the sidebar
OTHER = [
    ("Design sync", "20260924-151000-d4d4", [
        ("1", 4.0, "ال onboarding flow محتاج خطوة أقل، ال users بيقفلوا في الخطوة التالتة"),
        ("2", 3.6, "We can merge the profile step with the welcome screen"),
        ("1", 3.2, "تمام، اعمليلي prototype وأنا أعرضه على ال team")]),
    ("Client call: onboarding", "20260923-110500-e5e5", [
        ("1", 4.2, "أهلاً بحضرتك، النهارده هنمشي على ال setup خطوة خطوة"),
        ("2", 3.8, "Perfect, we mainly need the reports and the user roles"),
        ("1", 4.0, "ال roles موجودة في ال settings، هوريك دلوقتي")]),
    ("Weekly standup", "20260922-093000-f6f6", [
        ("1", 3.0, "امبارح خلصت ال payment integration"),
        ("2", 3.4, "أنا لسه شغال على ال search، فاضل ال filters"),
        ("3", 2.8, "No blockers from my side")]),
]
