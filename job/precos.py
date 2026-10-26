"""Baixa o COTAHIST anual oficial da B3 (ano atual e anterior) e grava dados/precos.json.

Fechamento do mercado à vista (TPMERC 010: ações, BDRs e ETFs; sem fracionário).
Preços ajustados só pelos eventos listados em config.json (ajustes_preco).
"""
import datetime as dt, io, json, pathlib, urllib.request, zipfile

UA = 'robo-acoes/1.0 (+https://github.com/marcelouehara/robo-acoes)'
RAIZ = pathlib.Path(__file__).resolve().parent.parent
CFG = json.loads((RAIZ / 'config.json').read_text())
ATIVOS = set(CFG['carteira'] + CFG['monitorar'])
URL = 'https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A{}.ZIP'


def baixar(ano):
    req = urllib.request.Request(URL.format(ano), headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=600) as r:
        return r.read()


def ler(zbytes, hist):
    z = zipfile.ZipFile(io.BytesIO(zbytes))
    with z.open(z.namelist()[0]) as f:
        for raw in f:
            l = raw.decode('latin-1')
            if l[:2] != '01' or l[24:27] != '010':
                continue
            t = l[12:24].strip()
            if t in ATIVOS:
                d = f'{l[2:6]}-{l[6:8]}-{l[8:10]}'
                hist.setdefault(t, {})[d] = int(l[108:121]) / 100


def main():
    ano = dt.date.today().year
    hist = {}
    for a in (ano - 1, ano):
        ler(baixar(a), hist)
    for t, eventos in CFG.get('ajustes_preco', {}).items():
        for data, fator, _ in eventos:
            for d in hist.get(t, {}):
                if d < data:
                    hist[t][d] = round(hist[t][d] * fator, 4)
    n = CFG['pregoes_no_historico']
    datas = sorted({d for v in hist.values() for d in v})[-n:]
    out = {
        'gerado_em': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        'fonte': 'B3 COTAHIST anual, mercado à vista',
        'ultimo_pregao': datas[-1],
        'datas': datas,
        'fechamento': {t: [hist.get(t, {}).get(d) for d in datas] for t in sorted(ATIVOS)},
        'faltando': sorted(ATIVOS - set(hist)),
    }
    (RAIZ / 'dados' / 'precos.json').write_text(json.dumps(out, separators=(',', ':')))
    print('precos.json:', out['ultimo_pregao'], len(datas), 'pregões', 'faltando:', out['faltando'])


if __name__ == '__main__':
    main()
