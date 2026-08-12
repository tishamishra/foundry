# -*- coding: utf-8 -*-
"""Patch location_intros in gen_data.py to hit 4-5 paragraphs / 190-230 words,
with genuinely distinct new content per item (no restated paragraphs)."""
import gen_data as d
import copy

location_intros = copy.deepcopy(d.location_intros)

# For idx 1 and 2 (already 5 paragraphs), extend one existing paragraph with a
# genuinely new clause rather than adding a 6th paragraph.
extend_idx1 = " The same logic applies to shaded elevations, which often show algae and moss well before an exposed elevation shows any comparable wear, for entirely different reasons rooted in how slowly each side actually dries out."
extend_idx2 = " It also means we generally know in advance which streets have narrow side access or awkward parking, so the right equipment gets brought on the first visit rather than causing a second one."

location_intros[1]["paras"][1] = location_intros[1]["paras"][1] + extend_idx1
location_intros[2]["paras"][2] = location_intros[2]["paras"][2] + extend_idx2

# One genuinely new, distinct paragraph appended per index (idx -> new text).
new_paragraphs = {
3: "That written finding stands on its own regardless of what you decide to do next. It is also worth keeping if the property changes hands, since a buyer's surveyor in {city} will often ask exactly the kind of question that an existing, dated inspection has already answered plainly.",

4: "The same continuity applies to materials. Ordering the wrong tile or slate for a {city} property, or having it arrive late, is one of the more common reasons a scheduled job slips, and working the area regularly means suppliers are already used to what a given street or estate typically needs.",

5: "Chimneys and abutments in particular reward that thoroughness, since a joint where brick meets covering rarely announces its own failure until water has already been travelling along a rafter or batten for some time, well away from the actual point it originally got in.",

6: "Every {city} job also comes with a written record — photographs from the roof, a specific scope, and a note of the materials actually used — kept on file afterward. That record is worth more than it sounds the first time an insurer, a surveyor, or a different roofer ever asks about your property's roof.",

7: "You are also told plainly what to do in the meantime if a genuine wait is involved before the crew arrives — where to place something under an active drip, and specifically what to avoid doing near a ceiling that is visibly bulging with trapped water overhead.",

8: "The written findings from that visit include photographs taken from the roof itself, not from the driveway, so you can see specifically what prompted whatever conclusion we reach about your {city} property, rather than simply being asked to take a stranger's word for the state of a surface you cannot see.",

9: "Roof ventilation is one thing that local familiarity actually helps with. A void that cannot breathe properly traps moisture against the underside of the covering, and {company} knows which {city} house types tend to be built with that problem baked in, saving time working it out from scratch on a first visit.",

10: "That local knowledge speeds up diagnosis considerably. It never replaces the inspection itself, because a roof that looks like every other one on a {city} street can still have a previous repair, an odd material substitution, or a detail nobody else nearby has ever needed touched at all.",

11: "Either way, {company} treats the property in front of us on its own evidence, not as an assumed copy of the house two doors down, however similar the two might genuinely look from the pavement on any given afternoon in {city}.",

12: "That flagging costs nothing and commits you to nothing. A homeowner deciding whether to have overhanging branches cut back is, in effect, making a roofing decision either way, and a written note from an inspection is often the first time anyone has actually pointed that connection out plainly.",

13: "Materials for a {city} job get named specifically in the written scope, not left as a vague reference to 'standard tiles' or 'the usual felt'. Being able to check exactly what was proposed against what was actually used afterward is a small thing that makes a genuine difference to trust.",

14: "The written assessment gets completed either way, regardless of the eventual outcome, so even a {city} visit that ends in honest advice to leave a roof alone for another year still leaves you with something dated and specific on file, rather than a half-remembered conversation from the driveway.",

15: "Where a repair does go ahead after a storm, {company} checks back after the next significant spell of rain to confirm it actually held, rather than treating the job as finished the moment the crew and their equipment leave the {city} property for the last time that week.",

16: "Fixings that have simply worked loose over years of ordinary movement are one of the more common, less dramatic reasons a {city} roof's remaining life turns out shorter than its age alone would suggest, and the inspection checks for that specifically across the whole roof rather than relying on age as a rough proxy for actual condition on its own.",

17: "{company} will tell you plainly if a {city} roof's specific pitch, material and exposure genuinely call for a different repair method than would be standard practice elsewhere in {county}, and will explain why in terms that do not require any prior knowledge of roofing to actually follow. That explanation is written into the scope itself, not just said once on the driveway and then forgotten by the time any questions come up later.",

18: "Parking and access get discussed before the day itself, not on the morning the crew turns up expecting a driveway that is actually blocked. {company} tells {city} customers plainly, in advance, exactly what needs to be clear, and for how long, so nobody is caught out at the last minute.",

19: "That pattern also shapes how {company} prices a {city} job before the inspection has even finished, since a known local specification narrows the likely range of outcomes considerably, even though the final number still depends entirely on what the visit itself actually confirms once someone is up there. Access equipment gets specified honestly at that same stage too, since guessing at it later, once a crew has already arrived and found a conservatory or a narrow side passage, is a common and avoidable cause of a job running longer than planned.",

20: "Call {phone}, describe what you have on the roof or think you have, and we will tell you honestly whether a visit this week actually makes sense or whether it is genuinely fine to wait until the weather settles a little in {city} first. Moss on its own is rarely the reason for that urgency; it becomes worth addressing once it starts holding consistent moisture against the covering or visibly lifting the edges of tiles, and {company} will tell you plainly which situation actually applies once someone has looked.",

21: "That first call also helps decide who from the crew is best suited to a given {city} job, since a suspected chimney flashing issue and a suspected ridge problem are not always best handled by exactly the same combination of people and equipment on the day. Loft insulation gets checked at the same visit wherever the job exposes it, because the two systems interact more than most people expect, and a roof problem is occasionally a ventilation problem wearing a roofing disguise.",

22: "Call {phone} and describe roughly what prompted the thought in the first place. Even a vague description — a patch of moss that looks bigger than last year, a slightly different sound in heavy rain — is usually enough to work out whether a visit genuinely makes sense yet. If the work does go ahead, it gets written up the way an insurer would actually want to read it, dated and specific about cause, whether or not a claim is ever involved at all.",

23: "We will show you the nearest realistic match available before committing you to anything, and explain plainly where it is likely to remain visible up close versus genuinely blend in from the street once the {city} job is finished. Valleys on an older property often need particular care during that kind of work too, since they collect more water and more debris than any other single detail on the roof and rarely forgive a rushed or approximate repair.",

24: "Ridge bedding gets checked as part of the same {city} visit as standard practice, since a ridge that has begun to move slightly as the structure underneath settles is usually still a straightforward fix, and considerably less so if it is simply left for another tenancy, or another owner entirely, to eventually notice on their own.",

25: "Gutters directly beneath a problem chimney get checked at the same {city} visit as standard, since debris shed by a failing flashing often ends up blocking the very drainage that would otherwise have carried it away safely, quietly turning one problem into a second, related one nearby before anyone involved has actually noticed either of them separately.",

26: "Grit on its own, without any other sign, rarely justifies urgent action. Combined with a roof's actual age and the number of previous repairs already carried out on it, though, it tells {company} roughly where a specific {city} property genuinely sits in its remaining practical service life. The fixings across the whole slope get checked at the same visit, not only the section nearest the gutter, since a run of fixings tends to age together rather than in isolated patches.",

27: "Roof ventilation gets checked at the same {city} visit wherever the job genuinely allows for it, since a void that cannot breathe properly adds its own slow, separate damage on top of whatever the storm has already caused, and the two causes are worth telling apart clearly in the written report rather than lumped together under a single, vague repair that addresses neither properly.",

28: "You get the reasoning behind that smaller {city} answer in writing too, with photographs taken directly from the roof, so the conclusion can genuinely be checked against the evidence rather than simply taken on trust from a stranger met once, briefly, on the telephone earlier that same week. That same written record gets written up the way an insurer would want to read it, in case the smaller job turns out to connect to a claim after all.",

29: "That said, the general pattern never replaces actually checking your own particular {city} roof directly, because two properties built in the very same year on the very same street can genuinely differ once someone has actually climbed up and looked closely at each of them in turn. Access matters here too — a period property with a narrow side return often needs different equipment from a similar-aged house with open access down the side, and that gets specified honestly upfront.",

30: "Getting that distinction right the first time saves a {city} customer from paying for roofing work that would never actually have resolved a ventilation problem in the first place, however reasonable the initial assumption felt from inside a house with an unexplained damp patch on the ceiling. Gutters get checked alongside the roof covering on the same visit as standard, since an overflowing gutter behind a fascia produces a remarkably similar-looking symptom for an entirely different, cheaper reason.",

31: "That range shows up most clearly in the written scope itself, which reads noticeably differently for a converted Victorian terrace in {city} than it reasonably would for a five-year-old new-build a few streets over, even where both properties happen to share a superficially similar roofline from the street. Loft insulation on the older property often needs a specific note in that same report, since it was rarely installed to a standard anyone would specify again today.",

32: "A roof that looks entirely ordinary from a {city} street can be quietly hiding a ventilation problem created years earlier by a loft conversion that was never properly reconciled with what the covering above it actually needs to keep working correctly through a normal run of seasons. After genuinely severe weather, {company} works through the resulting calls in order of how much water is actually entering a structure right now, confirmed by a short conversation on the phone rather than assumed.",

33: "Call {phone} and describe what is happening at both the roofline and the gutter beneath it, including details that seem unrelated at first, and the visit will settle which of the two systems is actually behind it. Ridge and hip bedding gets checked at the same time as standard practice on every {city} property, since it sits close to both and is worth confirming while a crew member is already up there working on the roof itself.",

34: "That same exposure assessment gets written into the file, so a future repair on the same {city} property starts from an already-established understanding of what the specific site actually needs, rather than a fresh crew working it out again from scratch on a later, unrelated visit. Moss tends to establish faster on the sheltered, shaded side of an exposed site than on the side taking the weather directly, which is worth noting for exactly the same reason.",

35: "None of those seasonal categories is treated as automatically more urgent than the others simply because of when it happens to occur during the year. A roof problem noticed in the height of a {city} summer gets exactly the same seriousness as one noticed mid-winter. Fixings that have quietly worked loose over a mild season are just as worth catching in July as a slipped tile is worth catching the morning after a January storm.",

36: "{company} will tell you plainly and honestly during the visit whether tree cover is currently the main factor at play on your {city} roof, or simply one detail worth noting for later alongside whatever else the inspection actually turns up once someone is properly up there looking. Valleys under mature trees get checked with particular care for exactly that reason, since they are where falling debris and standing water are most likely to combine into a genuine problem.",

37: "That helps most when sourcing a genuine match for older materials on a {city} property, since knowing roughly what sits under a comparable covering nearby narrows the search before it even properly begins in earnest. Ventilation on an older build often needs its own separate note in the same file too, since these properties were rarely designed with the airflow standards a modern new-build would use as standard today, in {city} or anywhere else in {county} for that matter.",

38: "Getting that distinction right the first time saves a {city} customer from paying for roofing work that would never actually have resolved a condensation problem in the first place, however reasonable the initial assumption felt from inside a house with an unexplained patch on an upstairs ceiling. Access to the loft space itself is usually the deciding factor in how quickly that distinction can actually be confirmed, which is part of why the first visit asks about it directly.",

39: "Gutters on older {city} properties frequently need a different fixing pattern from a modern equivalent nearby, and that difference gets accounted for honestly in the written scope rather than assumed away for the sake of convenience or a quicker, simpler quote written up in a hurry by somebody unfamiliar with the actual property.",

40: "Getting the ventilation right again afterward is usually a modest, contained piece of work once the actual cause has been correctly identified on a {city} property, rather than the larger job a customer might understandably have feared before anyone had actually explained what was really going on. Valleys near a converted loft space get checked with particular care too, since alterations to the internal layout occasionally change how water is meant to be shed from that section of roof.",

41: "That approach saves a {city} customer from paying for two separate visits when one, slightly longer, would have found the actual cause first time. After genuinely severe weather it matters even more, since a storm can affect both systems at once and treating them separately risks missing the connection between the two entirely, at real cost to whoever is paying for it.",

42: "That same assessment gets filed the way an insurer would actually want to read it — dated, specific, and tied to the particular exposure of that address — in case a future storm on the same {city} site ever needs to be referenced against it later, by us or by whoever ends up inspecting that particular roof next, whenever that turns out to be.",

43: "None of that means a summer call gets treated as less pressing than a winter one, or a quiet-looking property gets assumed to be at less risk than a visibly exposed one nearby. Access gets confirmed honestly at the point of booking too, regardless of season, so a {city} visit scheduled for a specific day does not get delayed by equipment nobody realised would be needed until the crew had already arrived at the property that morning.",

44: "{company} checks for tree-related factors on every relevant {city} visit as standard, even when the original call concerned something entirely unrelated to the trees themselves, because the connection between overhanging branches and a slow-developing roof problem is not always obvious until somebody actually points it out. Ridge lines under heavy tree cover get checked with particular attention too, since debris settling along a ridge is easy to miss from the ground and slow to clear naturally.",
}

for idx, para in new_paragraphs.items():
    location_intros[idx]["paras"].append(para)

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
    for i, item in enumerate(location_intros):
        total = sum(wc(p) for p in item["paras"])
        n = len(item["paras"])
        if not (4 <= n <= 5) or not (190 <= total <= 230):
            bad.append((i, n, total))
    print("total items:", len(location_intros))
    print("range bad:", len(bad))
    for b in bad:
        print(" range", b)

    dup = []
    for idx, item in enumerate(location_intros):
        paras = item["paras"]
        for i in range(len(paras)):
            for j in range(i + 1, len(paras)):
                s = sim(paras[i], paras[j])
                if s > 0.45:
                    dup.append((idx, i, j, round(s, 2)))
    print("similarity flags:", len(dup))
    for d_ in dup:
        print(" sim", d_)
