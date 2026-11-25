"""Extract local reference photos from the separate Muinsuskaitseamet Haapsalu Lisa 1 PDF.
Do not redistribute photographs without permission. Usage: python tools/extract_heritage.py path.pdf
"""
from pathlib import Path
import argparse,json,re
import fitz
def main():
    p=argparse.ArgumentParser();p.add_argument('pdf',type=Path);args=p.parse_args()
    out=Path(__file__).resolve().parents[1]/'data/raw/facades/heritage_local'
    out.mkdir(parents=True,exist_ok=True);rows=[];seen=set()
    with fitz.open(args.pdf) as doc:
        for pi,page in enumerate(doc):
            w=page.rect.width;words=page.get_text('words')
            if w<page.rect.height:continue
            for image in page.get_image_info():
                r=fitz.Rect(image['bbox'])
                if not (.375*w<=r.x0<=.43*w and .53*w<=r.x1<=.61*w and r.height>25):continue
                def col(a,b):
                    return [t for t in words if a*w<=(t[0]+t[2])/2<b*w and r.y0-2<=(t[1]+t[3])/2<=r.y1+2]
                ids=[t[4] for t in col(.327,.386) if re.fullmatch(r'\d{5,9}',t[4])]
                if len(set(ids))!=1:continue
                bid=ids[0]
                if bid in seen:continue
                seen.add(bid)
                addr=' '.join(t[4] for t in sorted(col(.222,.327),key=lambda a:(round(a[1]/3),a[0])))
                desc=' '.join(t[4] for t in sorted(col(.645,.95),key=lambda a:(round(a[1]/3),a[0])))
                name=bid+'.jpg';page.get_pixmap(matrix=fitz.Matrix(2,2),clip=r,alpha=False).save(out/name)
                rows.append(dict(etak_id=bid,address=addr,description=desc,image=name,page=pi+1))
    (out/'catalog.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Extracted {len(rows)} ETAK-linked reference photographs. Check row matches manually.')
    if not rows:raise SystemExit('No matches. Use separate May 2024 Lisa 1 landscape appendix.')
if __name__=='__main__':main()
