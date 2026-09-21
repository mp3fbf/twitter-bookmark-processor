# Piloto Jev: suficiência de contexto

Experimento autorizado por Roberto em 21/09/2026, após o piloto de prioridade.
Compara uma regra de metadados, Jev e o perfil `quick` do runner por assinatura
na decisão de recuperar conteúdo antes de responder a uma pergunta sobre um
bookmark. Não participa do pipeline de produção.

## Pergunta e referência

O primeiro piloto encontrou o próprio artigo que motivou o teste capturado
apenas como um link curto. Ambos os modelos o classificaram com prioridade
zero. Este experimento pergunta se os modelos conseguem distinguir informação
ausente de evidência suficiente para uma tarefa específica.

Foram selecionados 50 bookmarks públicos do snapshot anterior:

- 30 posts com uma afirmação textual verificável;
- 10 bookmarks com trechos de páginas públicas já capturadas pelo serviço;
- 10 casos naturais de informação ausente, incluindo vídeo com página contendo
  apenas navegação/rodapé, imagens, thread e artigo não capturado.

Cada uma das primeiras 40 fontes tem uma pergunta, um trecho de suporte e uma
âncora de resposta anotados pelo assistente. A preparação cria duas versões:
uma com a evidência e outra com o trecho removido. O código exige que a âncora
não permaneça em nenhum campo visível. Nos artigos, "suficiente" significa
suficiente para a pergunta, não uma cópia integral da página. As 90 entradas
são embaralhadas por hash e apresentadas individualmente, sem o par ao lado.

Essa referência é mais verificável do que concordância com outro modelo, mas
continua sendo uma amostra construída e anotada pelo assistente. Não é um
gabarito humano independente nem uma estimativa da qualidade em todo o acervo.
Os 10 casos naturais são reportados separadamente; o destino sugerido não
comprova que uma futura recuperação terá sucesso.

Os modelos recebem só a pergunta, texto público normalizado, autor, tipos de
mídia, URL da fonte, URL da página quando conhecida e trecho recuperado. Nunca
recebem variante, resposta, trecho de suporte, rótulo ou julgamento anterior.
As regras de suficiência são iguais para os dois modelos. Não há ferramentas
durante a inferência, download de mídia, envio de mensagens ou escrita em notas.

## Métricas pré-definidas

1. **Falso pronto:** decidir `ready` depois da remoção da evidência.
2. **Recuperação desnecessária:** pedir conteúdo quando o texto já responde.
3. **Destino:** acertar a rota anotada (`fetch_post`, `fetch_article`,
   `inspect_media`, `unresolved` ou `ready`).
4. **Par correto:** aceitar a versão suficiente e buscar a fonte correta na
   versão com o trecho removido.
5. Mediana/p90 de tempo e custo incremental.

O baseline determinístico v1 usa: trecho recuperado com pelo menos 120
caracteres → pronto; mídia → inspecionar mídia; indicação de thread → expandir
post; link → buscar página; post curto → expandir post; restante → pronto.
O limiar é uma hipótese simples congelada antes dos resultados, não uma regra
operacional validada. A comparação com "buscar sempre" pode ser calculada
diretamente: evita falsos prontos, mas busca sem necessidade nos 40 positivos.

`pairs_sufficiency_correct` mede a distinção entre informação presente e
ausente, independentemente do destino. `pairs_correct` também exige o destino
anotado. Outras rotas podem eventualmente recuperar a mesma informação; sem
executar essas buscas, a concordância de rota não equivale a erro comprovado de
recuperação.

A rubrica fica congelada antes da primeira chamada. Não há ajuste de prompt,
limiar ou seleção usando os resultados, nem repetição para escolher a melhor
resposta. Receipts interrompidos/falhos continuam reservando custo e nunca são
reexecutados automaticamente. Falta de autenticação não aciona fallback pago.

Após a rodada Jev, os cinco falsos prontos motivaram uma análise de erro
separada: perguntas mais explícitas nos mesmos cinco pares, em outro diretório.
Essa seleção é posterior aos resultados. Os dez novos casos não entram na
estimativa original e não constituem validação independente de melhoria. O
teto de US$ 0,10 cobre as duas rodadas juntas, incluindo reservas desconhecidas.

## Execução na Hetzner

Dados e anotações ficam fora do Git e da sincronização ativa, em diretório
privado. A anotação é uma lista de fontes com `state`, `kind` e, nos pares,
`evidence_field`, `evidence_span`, `answer_anchor`, `missing_route`. Casos
naturais têm `acceptable_routes`. `prepare` valida antes de criar a pasta.

```bash
python3 -m experiments.jev_context.pilot prepare \
  --annotations /workspace/_dev-worktrees/twitter-bookmark-processor/jev-context-annotations.json \
  --out /workspace/_dev-worktrees/twitter-bookmark-processor/jev-context-data

python3 -m experiments.jev_context.pilot rules --out <pasta>
python3 -m experiments.jev_context.pilot baseline --out <pasta> --limit 1
python3 -m experiments.jev_context.pilot jev --out <pasta> --limit 1
```

O agente carrega a chave canônica do 1Password em `TYPESAFE_API_KEY`, sem
stdout/argv. Jev usa somente `api.typesafe.ai`, sem redirecionamento de
credenciais, com `jev-1.13.0` fixado. O mesmo teto de US$ 0,10 do primeiro piloto
limita esta nova rodada autorizada. A reserva conservadora por chamada é
reutilizada do executor anterior. O preço é US$ 0,042 por milhão de tokens de
entrada; custo calculado pelo receipt, sem conciliação de fatura.

Após validar o contrato dos canários, execute sem `--limit`. Cada provedor
tem um lock próprio; pode executar ao mesmo tempo que outro provedor, mas não
em dois processos da mesma rota. O relatório lê os hashes congelados:

```bash
python3 -m experiments.jev_context.pilot report --out <pasta>
python3 -m pytest -q tests/test_jev_context.py tests/test_jev_pilot.py
ruff check experiments/jev_context tests/test_jev_context.py
```

As figuras usam `matplotlib` opcional, fora dos workers:

```bash
MPLCONFIGDIR=/tmp/jev-matplotlib python3 -m experiments.jev_context.plot_results \
  --data <pasta> --out <snapshot-final>
```

Brief visual: comparar falsos prontos e buscas desnecessárias dos três métodos,
em painéis com a mesma escala e denominador; posição e comprimento codificam
as contagens, seis rótulos sustentam a conclusão, sem cards ou legenda externa.
O script recusa publicar uma comparação incompleta e exporta desktop, celular,
SVG e CSV por caso. A análise das perguntas reformuladas permanece separada.

Só publique snapshots encerrados em `_inbox`. O relatório informa cobertura
parcial quando houver falha ou interrupção. Tempo inclui rede e, no runner,
inicialização do CLI; não mede o tempo de recuperação nem o pipeline completo.

## Contratos preservados

O proprietário do fluxo segue sendo `bookmark_automation`; a inferência de
produção continua exclusivamente por assinatura. A exceção paga é local a este
experimento e termina com sua avaliação. Nenhuma regra aqui é promovida à
produção. Todos os bookmarks permanecem preservados e elegíveis. O runner
publicado decide provider/modelo do baseline; o experimento registra o modelo
efetivo, sem alterar essa configuração.

Fontes: [Choice](https://docs.typesafe.ai/primitives/choice),
[modelos/preço](https://docs.typesafe.ai/models) e
[limitações](https://docs.typesafe.ai/model-jaggedness/jev-1.13).
