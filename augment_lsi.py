# -*- coding: utf-8 -*-
"""Patch location_service_intros in gen_data.py to hit 3-4 paragraphs / 140-180 words."""
import gen_data as d
import copy

location_service_intros = copy.deepcopy(d.location_service_intros)

new_paragraphs = {
0: "Chimney flashing accounts for a disproportionate share of the leaks {company} traces during {service_lower} in {city}, since the joint where brick meets covering is one of the hardest details on a roof to get exactly right and one of the easiest to get slightly wrong without it being obvious from below.",

1: "Gutters get checked at the same visit as standard, since an overflowing gutter behind a fascia in {city} can produce a damp patch that looks exactly like a roof leak from inside the house, and separating the two properly saves paying for {service_lower} that was never actually needed.",

2: "Ridge bedding gets checked on every {city} visit too, whether or not it was the reason for the original call, since a ridge that has begun to move as the structure settles is usually still a straightforward fix if caught early rather than left for a future owner to notice.",

3: "Materials get named specifically in the written {city} scope — manufacturer and product line — so what was proposed for {service_lower} can be checked afterward against what was actually used, rather than left as a vague reference that nobody can verify once the crew has packed up and gone.",

4: "Valleys get checked along their full length on every {city} visit, since they collect more water and more debris than any other single detail on a roof, and a valley quietly failing tends to produce a leak considerably worse than its actual size would suggest from the ground.",

5: "Where {service_lower} follows a storm, {company} checks back after the next significant spell of rain to confirm the {city} repair actually held, rather than treating the job as finished the moment the crew and their equipment have left the property for the final time that week.",

6: "That written record gets composed the way an insurer would actually want to read it — dated, photographed, and specific about cause — which matters most when {service_lower} in {city} follows storm damage rather than ordinary wear, even where no claim is ultimately made from it.",

7: "Access equipment for {service_lower} gets specified honestly at the {city} inspection stage rather than guessed at later, since a roof reachable easily from a driveway costs noticeably less to work than one over a conservatory or backing directly onto a neighbour's boundary fence nearby.",

8: "Fixings across the whole slope get checked on every {city} visit, not just the section near the reported fault, since a run of fixings tends to age together and one working loose is often an early, quiet sign of others nearby beginning to follow the same pattern.",

9: "Loft insulation gets checked wherever {service_lower} exposes it on a {city} property, because the two systems interact more than most people expect — insulation pushed too close to a cold roof can starve the void of airflow, encouraging the kind of condensation later mistaken for a leak.",

10: "Moss gets assessed rather than assumed either way during {service_lower} in {city}: cosmetic on a younger roof, worth addressing once it starts holding consistent moisture or lifting tile edges on an older one, and {company} will tell you honestly which situation actually applies to yours.",

11: "Scaffold and access equipment for a {city} job get erected, inspected while in use, and removed once the {service_lower} is complete and checked, with nothing left propped against the house overnight for longer than the job genuinely requires it to be there.",

12: "Tree cover changes what {service_lower} in {city} actually needs, since overhanging branches keep a covering damp for longer after rain and drop debris steadily into valleys and gutters, shortening the practical life of work that would otherwise have had considerably more years left in it.",

13: "A second opinion on {service_lower} in {city} costs an afternoon and occasionally saves a genuinely significant amount, particularly after a storm when urgency and inflated pricing have an unfortunate tendency to travel together in the same quote from a contractor working to a different set of incentives.",

14: "Chimneys get checked on every {city} visit as standard practice regardless of what specifically prompted the call, because a chimney flashing issue often explains a ceiling stain that otherwise looks entirely unrelated to it at first glance from inside the house.",

15: "Gutters directly below the working area get cleared as part of {service_lower} in {city}, not billed separately, since debris shed during the work itself is one of the more common and avoidable causes of a blocked downpipe discovered only after the crew has already left.",

16: "Ridge and hip bedding gets checked along the full length of a {city} roof during {service_lower}, re-pointed wherever it is found disturbed, since this detail sits close to several others worth confirming while a crew member is already safely up there working.",

17: "That record stays useful well beyond the immediate {service_lower} job in {city}. It becomes the baseline the next inspection gets compared against, whether that happens in a year, well beyond that, or sooner than anyone currently expects if conditions change.",

18: "Valleys get checked along their full length during {service_lower} on any {city} roof, since everything the rest of the covering sheds eventually funnels through them, and a valley quietly failing produces a leak considerably worse than its visible size from the ground would ever suggest.",

19: "Access equipment gets specified honestly at the same {city} inspection stage, since guessing at it later — once a crew has already arrived and found a conservatory or a narrow side passage — is a common and entirely avoidable cause of a job running longer than planned that week.",

20: "Moss gets assessed honestly rather than assumed either way during that same visit: cosmetic on a younger {city} roof, worth addressing once it starts holding consistent moisture or lifting tile edges on an older one, and {company} will say plainly which situation genuinely applies before proposing anything.",

21: "Loft insulation gets checked wherever {service_lower} exposes it, because the two systems interact more than most {city} homeowners expect, and a roof problem occasionally turns out to be a ventilation problem wearing a roofing disguise that only becomes obvious once someone actually looks underneath the covering.",

22: "Moss on its own rarely justifies the largest response to {service_lower} in {city}; it becomes worth treating once it starts holding consistent moisture against the covering or lifting tile edges, and {company} will say plainly which situation genuinely applies once the roof has actually been inspected.",

23: "That same written {city} scope states plainly what would happen if the deck underneath turned out softer than expected once the covering came off during {service_lower}, so a genuine, common variable never arrives as an unwelcome surprise buried in the final invoice instead.",

24: "Valleys get checked along their full length on the same {city} visit, whether or not they were the reason for the original call, since everything the rest of the covering sheds eventually funnels through them, and a valley quietly failing tends to produce a leak worse than its size suggests.",

25: "Gutters beneath the affected area get checked at the same time during {service_lower} in {city}, since debris shed by a failing detail above often ends up blocking the very drainage that would otherwise have carried it safely away from the property altogether.",

26: "The written {city} findings from {service_lower} name each elevation checked individually, rather than reporting on the roof as a single undifferentiated surface, because a prevailing wind direction tends to push damage into one side considerably harder than the others year after year.",

27: "That consistent sequence applies just as firmly after a genuinely severe storm in {city} as it does on a quiet Tuesday morning, because pricing {service_lower} from a phone description alone is how the wrong thing gets fixed confidently, regardless of how urgent the original call sounded.",

28: "That same triage applies to {service_lower} in {city} generally, not only in the immediate aftermath of a storm — active leaks are prioritised over cosmetic wear regardless of the season, and callers are told honestly, every time, exactly where they sit in the queue.",

29: "Two {city} properties needing what sounds like the same {service_lower} can require genuinely different approaches once pitch, existing materials and deck condition are actually accounted for, which is part of why a proper scope takes a little longer to arrive than a rough number given over the phone.",

30: "That wider check during {service_lower} occasionally uncovers a second, smaller issue on a {city} roof worth flagging even when it is not priced or actioned on the same visit, and you will always be told about it plainly, with a supporting photograph, so you can decide when.",

31: "Either starting point gets the same honest treatment on a {city} roof: {company} would rather confirm a specific, visible fault needs less than feared than let a vague worry about age turn into {service_lower} that the roof genuinely did not need yet this year.",

32: "Customers in {city} who have been quoted for {service_lower} elsewhere without that reasoning often notice the difference immediately once they see it written out properly, because a number without an explanation behind it is difficult to compare fairly against anything else, including a lower one from somewhere else.",

33: "If a {city} job's timeline for {service_lower} needs to shift because of genuinely bad weather, you are told about that as early as reasonably possible, well ahead of the day it was originally due to start, rather than discovering it only on the scheduled morning itself.",

34: "That additional loft check during {service_lower} regularly changes the final {city} scope for the better in one direction or the other, once daylight through the boards, damp insulation, or simple staining on the rafters has actually been accounted for properly rather than left unexamined.",

35: "You will be told honestly in {city} if a realistic timeline for {service_lower} runs longer than initially hoped, rather than being given a flattering date that later has to be quietly missed once the forecast, materials, or the roof itself turn out to have other plans entirely.",

36: "That written record matters most if a question comes up the following winter about whether a specific product used during {service_lower} was genuinely appropriate for that particular {city} roof's pitch and exposure, rather than simply whatever happened to be on the van that day.",

37: "That final walk-round is often the only real chance a {city} customer gets to ask about details that only become apparent once the scaffold has actually come down and {service_lower} can genuinely be seen properly, up close, in ordinary daylight rather than guessed at from the garden.",

38: "You will hear that honest, smaller answer directly, in writing, with photographs from the roof, so the conclusion about {service_lower} on your {city} property can genuinely be checked against the evidence rather than simply taken on trust from a voice on the phone.",
}

for idx, para in new_paragraphs.items():
    location_service_intros[idx]["paras"].append(para)

# add a 40th distinct item (original list only had 39)
location_service_intros.append(dict(paras=[
    "{service} in {city} almost always starts with the same honest question: is this contained to one spot, or the early, visible sign of something bigger across the roof.",
    "{company} answers that from the roof itself[[ across {city} and {nearby}]], in writing, with photographs, before any number is discussed with you on the phone or the doorstep.",
    "Most {city} calls about {service_lower} turn out to be the first kind rather than the second, once somebody has actually climbed up and looked properly at what is going on.",
    "Where it genuinely is the second kind, you are told that plainly too, with the reasoning behind it, rather than being left to find out only once the invoice arrives.",
]))

if __name__ == "__main__":
    def wc(s):
        return len(s.split())
    def sim(p1, p2):
        s1 = set(p1.lower().split())
        s2 = set(p2.lower().split())
        if not s1 or not s2:
            return 0
        return len(s1 & s2) / min(len(s1), len(s2))

    bad = []
    for i, item in enumerate(location_service_intros):
        total = sum(wc(p) for p in item["paras"])
        n = len(item["paras"])
        if not (3 <= n <= 4) or not (140 <= total <= 180):
            bad.append((i, n, total))
    print("total items:", len(location_service_intros))
    print("range bad:", len(bad))
    for b in bad:
        print(" range", b)

    dup = []
    for idx, item in enumerate(location_service_intros):
        paras = item["paras"]
        for i in range(len(paras)):
            for j in range(i + 1, len(paras)):
                s = sim(paras[i], paras[j])
                if s > 0.45:
                    dup.append((idx, i, j, round(s, 2)))
    print("similarity flags:", len(dup))
    for d_ in dup:
        print(" sim", d_)
