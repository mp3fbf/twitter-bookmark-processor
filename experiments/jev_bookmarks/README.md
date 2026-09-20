# Piloto Jev para priorização de bookmarks

Experimento autorizado por Roberto em 19/09/2026. Compara Jev com o perfil
`quick` do runner por assinatura, usando exatamente o mesmo texto público,
interesses e critérios. Mede concordância, itens relevantes rebaixados, tempo
por item e custo incremental. O piloto não participa dos workers ou timers.

## Hipótese e limite da comparação

Jev pode ser útil para decisões delimitadas: tema, prioridade de leitura e
necessidade de buscar mais contexto. A rubrica em `rubric.json` é uma hipótese
experimental. Ela não muda a regra canônica: todos os bookmarks são preservados
e continuam elegíveis para processamento.

As triagens históricas têm resumos e temas livres, sem prioridade comparável.
Elas identificam a amostra, mas **não são gabarito humano**. O baseline precisa
responder à mesma rubrica nova. A concordância entre dois modelos não comprova
acerto. Os cinco cliques humanos encontrados na preparação são decisões
operacionais (`act`, `keep`, `skip`), não rótulos equivalentes de relevância.
O relatório os apresenta individualmente para revisão, sem inferir acurácia.

O preparador seleciona a última triagem concluída de cada bookmark e congela o
snapshot efetivamente usado naquela triagem. Versões repetidas não multiplicam
a amostra. Uma ordenação por hash destina 20% à calibração e o restante a um
holdout separado. O conjunto contém apenas itens que o sistema já conseguiu
triar, portanto não representa falhas de captura nem todos os 1.457 bookmarks.

Os dois modelos recebem somente texto, autor, tipos de mídia e eventuais
trechos citados/prévias de artigo. Imagens, vídeos e destinos de links não são
abertos; `needs_context` explicita essa limitação. Referências históricas,
decisões humanas, notas privadas e contexto do workspace nunca entram no input.

O baseline retorna um nível de prioridade entre 0 e 3; Jev retorna a média
probabilística nesses níveis. O relatório preserva essa diferença, mostra o
erro absoluto e compara a regra experimental `priority >= 2` nos dois lados.
Casos relevantes rebaixados são listados com URL para revisão. O campo
`confidence` nunca é tratado como probabilidade de acerto.

## Preparação e execução na Hetzner

Execute a partir da raiz do repositório. A preparação e o relatório são locais.
Os arquivos pessoais gerados ficam fora do Git, com permissões privadas:

```bash
python3 -m experiments.jev_bookmarks.pilot prepare \
  --db /workspace/twitter-bookmark-processor/data/bookmark-automation.sqlite3 \
  --out /workspace/_inbox/jev-bookmark-pilot-20260919

python3 -m experiments.jev_bookmarks.pilot baseline \
  --out /workspace/_inbox/jev-bookmark-pilot-20260919 --limit 1
```

O primeiro baseline é um canário real de contrato, não uma medida de qualidade.
O runner publicado mantém provider/modelo na configuração e autenticação por
assinatura. Seu consumo de quota não equivale a custo zero; o custo incremental
de API é zero. Não há fallback pago para esse baseline.

Para Jev, obtenha acesso e uma chave em <https://console.typesafe.ai/> e guarde-a
no 1Password. Informe ao agente apenas a referência `op://...`. O agente carrega
a chave em `TYPESAFE_API_KEY` sem imprimi-la nem passá-la em argv. O executor
usa somente o endpoint oficial, com modelo fixado em `jev-1.13.0`:

```bash
python3 -m experiments.jev_bookmarks.pilot jev \
  --out /workspace/_inbox/jev-bookmark-pilot-20260919 --limit 1
```

Depois de conferir o canário, rode os dois comandos sem `--limit` para completar
a calibração. Use `--split holdout` para a avaliação final. Retomadas pulam
identidades já tentadas; falhas não são reexecutadas automaticamente. Uma
tentativa interrompida permanece registrada e conserva sua reserva de custo.
Não apague receipts para repetir chamadas; crie outro experimento e contabilize
o anterior. Alterar a amostra ou a rubrica invalida os hashes e bloqueia a execução.

O orçamento padrão de US$ 0,10 limita este piloto pequeno e pode ser reduzido
por `--budget-usd`. Cada tentativa reserva antes da chamada o máximo documentado
de 64 mil tokens a US$ 0,042/milhão (US$ 0,002688); depois substitui a reserva
pelo uso informado. Falhas sem uso conhecido conservam o máximo. Não há retry
oculto nem concorrência entre execuções da mesma pasta.

```bash
python3 -m experiments.jev_bookmarks.pilot report \
  --out /workspace/_inbox/jev-bookmark-pilot-20260919
```

Tempos incluem rede e overhead do cliente. No baseline também incluem startup
do runner/CLI. Logo, a comparação mede as rotas disponíveis no workspace, não
latência pura dos modelos. Relate tamanho e cobertura do holdout, falhas e
divergências antes de propor qualquer adoção. Examine também os casos em
português: esta versão não possui rótulos humanos de idioma ou qualidade.

## Escopo e controles

- O banco operacional é aberto com `mode=ro`; fila, notas e Telegram permanecem
  sob controle exclusivo do serviço canônico `bookmark_automation`.
- A exceção de API paga vale somente para o experimento Jev autorizado nesta
  conversa e se encerra com sua avaliação. Não muda a política de inferência por
  assinatura da produção nem sua configuração.
- Nenhuma integração, instalação global de skill, timer ou publicação externa
  é feita pelo executor. Jev recebe apenas a amostra pública delimitada.
- Este executor implementa a API direta TypeSafe; OpenRouter não está implementado.
- Resultados são arquivos gerados pessoais. Só código, rubrica e testes vão ao Git.

Verificação local:

```bash
python3 -m pytest -q tests/test_jev_pilot.py
ruff check experiments/jev_bookmarks tests/test_jev_pilot.py
```

Fontes verificadas em 19/09/2026 (BRT): [API](https://docs.typesafe.ai/api),
[modelos e preço](https://docs.typesafe.ai/models),
[Score](https://docs.typesafe.ai/primitives/score),
[limitações](https://docs.typesafe.ai/model-jaggedness/jev-1.13).
