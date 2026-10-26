# robo-acoes

Dados públicos para o robô de ações (n8n): preços diários da B3 e indicadores fundamentalistas da CVM.
Atualizado pelo GitHub Actions (`.github/workflows/dados.yml`), sem nenhuma chave ou token externo.

| Arquivo | Conteúdo | Fonte |
|---|---|---|
| `dados/precos.json` | Últimos 320 fechamentos do mercado à vista de cada ativo | COTAHIST anual da B3 |
| `dados/fundamentos.json` | P/L, P/VP, ROE, margem, dívida líquida/EBITDA, crescimento do lucro e nota de 0 a 4 | DFP/ITR/FCA da CVM |

Ativos, financeiras e ajustes de preço (desdobramento/grupamento) ficam em `config.json`.

Regras da nota em `job/fundamentos.py`. Limitações conhecidas:
- a CVM não informa a unidade da quantidade de ações; quando o P/VP sai abaixo de 0,05, o job assume que veio em milhares;
- bancos e seguradoras não têm dívida líquida/EBITDA, a nota delas vai até 3;
- BDRs e ETFs não têm balanço na CVM;
- lucro em 12 meses inclui eventos não recorrentes, o que pode distorcer o pilar "momento".

Não é recomendação de investimento.
