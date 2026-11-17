#!/usr/bin/env python3
"""visual_kit.py - build a reusable kitchen example Visual Kit with code-drawn chalk-chef and printable PDFs.
Usage: visual_kit.py OUTDIR  (new version dir only; existing kit.json is never overwritten)
Adapt shots/templates to any business. Uses vkit.py; no AI generations or online dependencies."""
from pathlib import Path
import json, hashlib, sys
from PIL import Image, ImageDraw
import vkit as v

def build(root):
    root = Path(root); assert not (root / 'kit.json').exists(), 'Kit versions are immutable; choose a new version dir'
    for d in ('refs', 'shots', 'graphics', 'downloads'): (root / d).mkdir(parents=True, exist_ok=True)
    B = v.board(1920,1080); B.convert('RGB').save(root / 'graphics/chalkboard.jpg', quality=93)
    (root / 'character_block.txt').write_text('CHALK CHEF: fictional, code-drawn adult mascot; chalk-white round head and chef coat; puffy white toque; yellow split moustache and yellow-outlined apron; pink cheeks; sky-blue neckerchief; blackboard fill. Never based on a real person. No mouth motion over voiceover. Closed smile is the reference.\n')
    (root / 'style_lock.txt').write_text('Chalkboard business explainer: deep green slate #22302B, chalk-white #F0EEE4, warm yellow #F7D65C, sky blue #82C4F0; hand-drawn chalk grain, simple diagram shapes, locked camera, no photographic faces or real brands. Use the same vkit.chef function and versioned refs.\n')
    for pose in ('idle', 'point', 'wave', 'thumbs'):
        im = v.board(800, 800); L = Image.new('RGBA', im.size, (0,0,0,0)); d=ImageDraw.Draw(L); v.chef(d, 340, 420, 1.0, pose=pose, mouth=0)
        v.chalkify(L); im.alpha_composite(L); im.convert('RGB').save(root / f'refs/chef_{pose}.jpg', quality=92)
    for nm in ('line_stations', 'walk_in', 'prep_list', 'ticket_rail'):
        im = B.copy(); L = Image.new('RGBA', im.size, (0,0,0,0)); d = ImageDraw.Draw(L)
        v.ctext(d, (100, 80), nm.replace('_',' ').upper(), 88, 'yellow')
        if nm == 'line_stations': v.stations(d,100,320,1700,420,['GRILL','FRY','SAUTE'],s=1.8)
        elif nm == 'walk_in': v.walk_in(d,550,290,800,620,s=1.5)
        elif nm == 'prep_list': v.prep_list(d,500,260,900,[('Patties  |  par 40',''),('Buns  |  par 60',''),('Fries  |  par 30','')],s=1.7)
        else: v.ticket_rail(d,100,330,1700,12,hot=4,s=1.4)
        v.chalkify(L); im.alpha_composite(L); im.convert('RGB').save(root / f'shots/{nm}.jpg', quality=90)
    footer = 'Free kitchen example | Adapt to your business and local requirements | Grok Bot Video Studio'
    checklist = [('Friday rush readiness', ['Done','Task','Owner','Time'], [40,296,110,70],
        [['[ ]','Verify station mise en place','',''],['[ ]','Check critical stock against par','',''],['[ ]','Assign expo and handoff roles','',''],['[ ]','Review expected covers and bottlenecks','','']], 10)]
    v.printable(root / 'downloads/checklist.pdf', 'Shift readiness checklist', 'Kitchen example - edit the tasks, then print. Blank rows are yours.', checklist, footer)
    oc_cols, oc_w = ['Done','Task','Initials','Time'], [40,326,80,70]
    op = [['[ ]','Review prep list and station assignments','',''],['[ ]','Check equipment and record required checks','',''],['[ ]','Stock stations to agreed par levels','',''],['[ ]','Brief the team on service and handoffs','','']]
    cl = [['[ ]','Label, store and record stock per site rules','',''],['[ ]','Count key items and note waste','',''],['[ ]','Clean and reset each station','',''],['[ ]','Note repairs and hand off tomorrow\'s prep','','']]
    v.printable(root / 'downloads/opening_closing.pdf', 'Opening + closing list', 'Kitchen example - use your site rules for safety, storage and checks.', [('Opening',oc_cols,oc_w,op,4),('Closing',oc_cols,oc_w,cl,4)], footer)
    v.printable(root / 'downloads/par_levels.pdf', 'Par-level sheet', 'Kitchen example - enter units; order = max(0, par - on hand - on order).',
        [('', ['Item','Unit','Par','On hand','On order','To order'], [176,60,60,70,75,75], [['','','','','',''] for _ in range(18)],0)], footer)
    (root / 'README.md').write_text('# Kitchen Visual Kit v1 (example)\n\n0 generations. Chalk Chef is a fictional code-drawn mascot, not the owner. Original diagrams and PDFs made in code.\nRefs and shots are review assets, not owner-approved for a real project. No real faces or customer names. Reuse kit assets rather than regenerating.\n\nFree downloads: `downloads/checklist.pdf`, `downloads/opening_closing.pdf`, `downloads/par_levels.pdf`. Local files only; no hosted link has been invented. After owner approval of a hosted URL, add that exact URL to a post draft. Offer these files with each video.\n\nTemplates are editable starting points, not food-safety, legal or staffing advice. Follow local/site rules.\n\nGenerator: kit-src/visual_kit.py, shared drawing code: kit-src/vkit.py. Change the business and diagrams for another industry; put changes in a new kit version.\n')
    assets = []
    for p in sorted(root.rglob('*')):
        if p.is_file(): assets.append({'id':p.stem,'path':str(p.relative_to(root)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'license':'original code-drawn / templates','route':'code','generation_count':0})
    kit = {'id':'kitchen-chalk','version':1,'status':'example-review','business':'restaurant / kitchen (worked example; adapt to any business)',
           'character':{'type':'fictional-mascot','ref':'refs/chef_idle.jpg','block':'character_block.txt','consent_required':False},
           'style_lock':'style_lock.txt','renderer':'vkit.py','assets':assets,'generation_count':0,'license':'original code-drawn; no third-party photos, footage or music',
           'downloads':['downloads/checklist.pdf','downloads/opening_closing.pdf','downloads/par_levels.pdf'],'reuse_policy':'reuse approved assets unchanged; version edits; log used_in in project manifest'}
    (root/'kit.json').write_text(json.dumps(kit,indent=2)+'\n')
    thumbs=[]
    for p in list((root/'refs').glob('*.jpg'))+list((root/'shots').glob('*.jpg')):
        im=Image.open(p).convert('RGB'); im.thumbnail((480,300)); c=Image.new('RGB',(480,340),(25,35,30)); c.paste(im,((480-im.width)//2,0)); d=ImageDraw.Draw(c); d.text((20,305),p.stem,font=v.font('sans',22),fill='white'); thumbs.append(c)
    sh=Image.new('RGB',(1920,680))
    for i,im in enumerate(thumbs): sh.paste(im,((i%4)*480,(i//4)*340))
    sh.save(root/'contact_sheet.jpg',quality=90)
    print(root/'kit.json')
if __name__=='__main__': build(sys.argv[1])
