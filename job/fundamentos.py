"""Calcula indicadores fundamentalistas a partir dos dados abertos da CVM e grava dados/fundamentos.json.

Fontes (públicas, sem autenticação):
  - DFP (anual) e ITR (trimestral) consolidados: https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/
  - FCA valor mobiliário: liga ticker -> CNPJ
  - Preço: dados/precos.json (gerado por precos.py)

Indicadores (lucro, receita, EBITDA em 12 meses; balanço do último trimestre):
  P/L, P/VP, ROE, margem líquida, dívida líquida / EBITDA, crescimento do lucro em 12 meses.

Nota (pilares, 1 ponto cada):
  preço     0 < P/L <= 15
  qualidade ROE >= 12%
  risco     dívida líquida / EBITDA <= 3 (ou caixa líquido)   [não se aplica a financeiras]
  momento   lucro 12m > 0 e maior que o de 1 ano antes (sensível a eventos não recorrentes)
Aprovada quando faz pelo menos 2/3 dos pontos possíveis (3/4, ou 2/3 nas financeiras).
"""
import csv, datetime as dt, io, json, pathlib, re, urllib.error, urllib.request, zipfile

UA = 'robo-acoes/1.0 (+https://github.com/marcelouehara/robo-acoes)'
RAIZ = pathlib.Path(__file__).resolve().parent.parent
CFG = json.loads((RAIZ / 'config.json').read_text())
BASE = 'https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC'
FIN = set(CFG['financeiras'])
TICKERS = [t for t in CFG['carteira'] + CFG['monitorar'] if t not in CFG['sem_balanco']]


def baixar(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=600) as r:
            return zipfile.ZipFile(io.BytesIO(r.read()))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def linhas(z, nome):
    if z is None or nome not in z.namelist():
        return
    with z.open(nome) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding='latin-1'), delimiter=';')


def norm(s):
    return re.sub(r'\s+', ' ', s.strip().lower())


class Demonstracoes:
    """Guarda, por CNPJ, os valores de cada conta: (demonstração, cd_conta, ds_conta, dt_ini, dt_fim) -> valor em R$."""

    def __init__(self, cnpjs):
        self.cnpjs = cnpjs
        self.v = {}          # cnpj -> {(dem, dt_ini, dt_fim): [(cd, ds, valor)]}
        self.versao = {}     # (cnpj, dt_refer) -> maior versão vista

    def carregar(self, z, prefixo, ano):
        for dem in ('DRE', 'BPA', 'BPP', 'DFC_MI'):
            nome = f'{prefixo}_cia_aberta_{dem}_con_{ano}.csv'
            rows = [r for r in linhas(z, nome) if r['CNPJ_CIA'] in self.cnpjs]
            maxv = {}
            for r in rows:
                k = (r['CNPJ_CIA'], r['DT_REFER'])
                maxv[k] = max(maxv.get(k, 0), int(r['VERSAO']))
            for r in rows:
                if int(r['VERSAO']) != maxv[(r['CNPJ_CIA'], r['DT_REFER'])]:
                    continue
                esc = 1000 if r['ESCALA_MOEDA'].upper() == 'MIL' else 1
                chave = (dem, r.get('DT_INI_EXERC') or None, r['DT_FIM_EXERC'])
                self.v.setdefault(r['CNPJ_CIA'], {}).setdefault(chave, {})[r['CD_CONTA']] = (r['DS_CONTA'], float(r['VL_CONTA']) * esc)

    def contas(self, cnpj, dem, dt_ini, dt_fim):
        return self.v.get(cnpj, {}).get((dem, dt_ini, dt_fim), {})

    def periodos(self, cnpj, dem='DRE'):
        return sorted({(i, f) for (d, i, f) in self.v.get(cnpj, {}) if d == dem})


def achar(contas, padrao, nivel=None, filho_de=None):
    """Soma das contas cujo nome casa com o padrão (regex), opcionalmente num nível ou abaixo de uma conta."""
    tot, achou = 0.0, False
    for cd, (ds, val) in contas.items():
        if nivel is not None and cd.count('.') + 1 != nivel:
            continue
        if filho_de and not cd.startswith(filho_de + '.'):
            continue
        if re.search(padrao, norm(ds)):
            tot += val; achou = True
    return tot if achou else None


def primeiro(contas, padrao, nivel):
    cand = sorted((cd, ds, v) for cd, (ds, v) in contas.items() if cd.count('.') + 1 == nivel and re.search(padrao, norm(ds)))
    return cand[0] if cand else None


def fluxo(D, cnpj, dt_fim):
    """Valores acumulados no ano (YTD) até dt_fim: lucro (controlador), receita, EBIT e D&A."""
    ini = dt_fim[:4] + '-01-01'
    dre = D.contas(cnpj, 'DRE', ini, dt_fim)
    dfc = D.contas(cnpj, 'DFC_MI', ini, dt_fim)
    if not dre:
        return None
    lucro_cons = primeiro(dre, r'^lucro.*consolidado do per[ií]odo|^lucro/preju[ií]zo do per[ií]odo', 2)
    lucro = None
    if lucro_cons:
        lucro = achar(dre, r'controladora', filho_de=lucro_cons[0])
        if lucro is None:
            lucro = lucro_cons[2]
    return {
        'lucro': lucro,
        'receita': dre['3.01'][1] if '3.01' in dre else None,
        'ebit': achar(dre, r'^resultado antes do resultado financeiro e dos tributos', nivel=2),
        'da': achar(dfc, r'deprecia|amortiza|exaust', filho_de='6.01.01'),
    }


def ttm(D, cnpj, dt_fim):
    """Soma de 12 meses terminando em dt_fim: ano cheio, ou ano anterior + YTD atual - YTD do ano anterior."""
    atual = fluxo(D, cnpj, dt_fim)
    if atual is None:
        return None
    if dt_fim.endswith('-12-31'):
        return atual
    ano = int(dt_fim[:4])
    cheio = fluxo(D, cnpj, f'{ano - 1}-12-31')
    antes = fluxo(D, cnpj, f'{ano - 1}{dt_fim[4:]}')
    if cheio is None or antes is None:
        return None
    out = {}
    for k in atual:
        vals = (cheio[k], atual[k], antes[k])
        out[k] = None if any(v is None for v in vals) else cheio[k] + atual[k] - antes[k]
    return out


def balanco(D, cnpj, dt_fim):
    bpa = D.contas(cnpj, 'BPA', None, dt_fim) or D.contas(cnpj, 'BPA', dt_fim[:4] + '-01-01', dt_fim)
    bpp = D.contas(cnpj, 'BPP', None, dt_fim) or D.contas(cnpj, 'BPP', dt_fim[:4] + '-01-01', dt_fim)
    if not bpp:
        return None
    pl_cons = primeiro(bpp, r'^patrim[oô]nio l[ií]quido', 2)
    pl = None
    if pl_cons:
        nc = achar(bpp, r'n[aã]o controladores', filho_de=pl_cons[0])
        pl = pl_cons[2] - (nc or 0)
    divida = achar(bpp, r'^empr[eé]stimos e financiamentos', nivel=3)
    caixa = sum(v for v in (achar(bpa, r'^caixa e equivalentes', nivel=3), achar(bpa, r'^aplica[cç][oõ]es financeiras', nivel=3)) if v)
    return {'pl': pl, 'divida': divida, 'caixa': caixa}


def main():
    hoje = dt.date.today()
    ano = hoje.year
    # ticker -> CNPJ pelo FCA mais recente
    mapa = dict(CFG.get('cnpj_manual', {}))   # casos em que o FCA vem sem o código (ex.: BTG declara a unit como 000000)
    for a in (ano, ano - 1):
        z = baixar(f'{BASE}/FCA/DADOS/fca_cia_aberta_{a}.zip')
        for r in linhas(z, f'fca_cia_aberta_valor_mobiliario_{a}.csv'):
            if r['Codigo_Negociacao'] in TICKERS and r['Codigo_Negociacao'] not in mapa:
                mapa[r['Codigo_Negociacao']] = r['CNPJ_Companhia']
        if len(mapa) == len(TICKERS):
            break
    cnpjs = set(mapa.values())

    D = Demonstracoes(cnpjs)
    acoes = {}
    for prefixo, a in (('dfp', ano - 1), ('dfp', ano), ('itr', ano - 1), ('itr', ano)):
        z = baixar(f'{BASE}/{prefixo.upper()}/DADOS/{prefixo}_cia_aberta_{a}.zip')
        if z is None:
            continue
        D.carregar(z, prefixo, a)
        for r in linhas(z, f'{prefixo}_cia_aberta_composicao_capital_{a}.csv'):
            if r['CNPJ_CIA'] in cnpjs:
                k = (r['DT_REFER'], int(r['VERSAO']))
                if r['CNPJ_CIA'] not in acoes or k > acoes[r['CNPJ_CIA']][0]:
                    acoes[r['CNPJ_CIA']] = (k, int(r['QT_ACAO_TOTAL_CAP_INTEGR'] or 0) - int(r['QT_ACAO_TOTAL_TESOURO'] or 0))

    precos = json.loads((RAIZ / 'dados' / 'precos.json').read_text())
    def ultimo_preco(t):
        serie = [p for p in precos['fechamento'].get(t, []) if p is not None]
        return serie[-1] if serie else None

    out = {}
    for t in TICKERS:
        cnpj = mapa.get(t)
        r = {'cnpj': cnpj, 'financeira': t in FIN}
        if not cnpj:
            r['erro'] = 'ticker sem CNPJ no FCA'; out[t] = r; continue
        periodos = [f for (i, f) in D.periodos(cnpj) if i and i.endswith('-01-01')]
        if not periodos:
            r['erro'] = 'sem demonstrações'; out[t] = r; continue
        fim = max(periodos)
        f12 = ttm(D, cnpj, fim)
        fim_ant = f'{int(fim[:4]) - 1}{fim[4:]}'
        f12_ant = ttm(D, cnpj, fim_ant)
        b = balanco(D, cnpj, fim)
        preco, qt = ultimo_preco(t), acoes.get(cnpj, (None, None))[1]
        lucro = f12 and f12['lucro']
        pl_ = b and b['pl']
        # A CVM não informa a unidade da quantidade de ações: algumas empresas declaram em milhares.
        # Se o P/VP resultante for absurdo (< 0,05), a quantidade está em milhares.
        if preco and qt and pl_ and pl_ > 0 and preco * qt / CFG.get('acoes_por_unit', {}).get(t, 1) / pl_ < 0.05:
            qt *= 1000
            r['acoes_em_milhares'] = True
        # Units (ex.: BPAC11 = 1 ON + 2 PN): o preço é da unit, então divide a quantidade de ações por ações/unit
        if qt and t in CFG.get('acoes_por_unit', {}):
            qt = qt / CFG['acoes_por_unit'][t]
        mcap = preco * qt if preco and qt else None
        ind = {
            'p_l': mcap / lucro if mcap and lucro and lucro > 0 else None,
            'p_vp': mcap / pl_ if mcap and pl_ and pl_ > 0 else None,
            'roe': lucro / pl_ if lucro is not None and pl_ and pl_ > 0 else None,
            'margem': (lucro / f12['receita'] if not r['financeira'] and lucro is not None and f12['receita'] else None),
            'div_ebitda': None,
            'cresc_lucro': (lucro / f12_ant['lucro'] - 1 if lucro is not None and f12_ant and f12_ant['lucro'] and f12_ant['lucro'] > 0 else None),
        }
        if not r['financeira'] and f12 and b and f12['ebit'] is not None:
            ebitda = f12['ebit'] + (f12['da'] or 0)
            liq = (b['divida'] or 0) - (b['caixa'] or 0)
            ind['div_ebitda'] = liq / ebitda if ebitda > 0 else (None if liq > 0 else -1.0)
        pil = {
            'preco': ind['p_l'] is not None and ind['p_l'] <= 15,
            'qualidade': ind['roe'] is not None and ind['roe'] >= 0.12,
            'momento': lucro is not None and lucro > 0 and ind['cresc_lucro'] is not None and ind['cresc_lucro'] > 0,
        }
        if not r['financeira']:
            pil['risco'] = ind['div_ebitda'] is not None and ind['div_ebitda'] <= 3
        pontos, maximo = sum(pil.values()), len(pil)
        r.update({
            'periodo': fim, 'preco': preco, 'acoes': qt, 'valor_mercado': mcap,
            'lucro_12m': lucro, 'lucro_12m_ano_anterior': f12_ant and f12_ant['lucro'],
            'indicadores': {k: (round(v, 4) if isinstance(v, float) else v) for k, v in ind.items()},
            'pilares': pil, 'pontos': pontos, 'maximo': maximo, 'aprovada': pontos * 3 >= maximo * 2,
        })
        out[t] = r

    res = {
        'gerado_em': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        'fonte': 'CVM dados abertos (DFP/ITR consolidados, FCA) + preço B3',
        'preco_de': precos['ultimo_pregao'],
        'regras': __doc__.split('Nota (pilares, 1 ponto cada):')[1].strip(),
        'empresas': out,
    }
    (RAIZ / 'dados' / 'fundamentos.json').write_text(json.dumps(res, ensure_ascii=False, indent=1))
    for t, r in out.items():
        i = r.get('indicadores', {})
        f = lambda v, p=False: '-' if v is None else (f'{v*100:.0f}%' if p else f'{v:.1f}')
        print(f"{t:7} {r.get('periodo','-')} P/L {f(i.get('p_l'))} P/VP {f(i.get('p_vp'))} ROE {f(i.get('roe'),1)} marg {f(i.get('margem'),1)} DL/EBITDA {f(i.get('div_ebitda'))} cresc {f(i.get('cresc_lucro'),1)} -> {r.get('pontos')}/{r.get('maximo')} {r.get('erro','')}")


if __name__ == '__main__':
    main()
