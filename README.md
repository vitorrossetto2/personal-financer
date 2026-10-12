# NovoSaldo

Aplicação local de controle financeiro pessoal, criada com Flask e SQLite.

## Como executar

```powershell
uv sync
uv run python run.py
```

No uso pessoal local, defina `NOVOSALDO_LOCAL_AUTH_DISABLED=true` no `.env` para abrir o sistema sem login. Essa opção só dispensa a autenticação para conexões locais (`127.0.0.1`, `localhost` ou `::1`); acessos vindos de outro computador continuam protegidos.

Abra `http://127.0.0.1:5000` no navegador. O banco é criado automaticamente em `instance/financeiro.db` e já inclui Itaú e Bradesco.

Caso ainda não tenha o `uv`, instale-o seguindo as instruções em `https://docs.astral.sh/uv/`.

## Configurar o login com Google

1. No Google Cloud Console, crie ou selecione um projeto e configure a tela de consentimento OAuth.
2. Crie uma credencial **ID do cliente OAuth**, do tipo **Aplicativo da Web**.
3. Adicione esta URI de redirecionamento autorizada para uso local:

   `http://127.0.0.1:5000/auth/google/callback`

4. Copie `.env.example` para `.env`, preencha `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` e informe em `NOVOSALDO_ALLOWED_EMAIL` a única conta Google autorizada.
5. Gere uma chave de sessão e coloque o resultado em `NOVOSALDO_SECRET_KEY`:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

O arquivo `.env` é ignorado pelo Git. Em uma publicação real, cadastre também a URI HTTPS exata do site no Google Cloud, defina `NOVOSALDO_HTTPS=true` e mantenha as quatro variáveis como segredos do ambiente. A URI precisa corresponder exatamente à cadastrada no Google. O endpoint MCP é ativado automaticamente somente no endereço local; mantenha `NOVOSALDO_MCP_ENABLED=false` ao publicar o site. O Cloudflare não inclui o servidor MCP e responde 404 em `/mcp`.

Sem essas configurações, o sistema permanece bloqueado e a tela de login indica quais valores estão faltando.

## Operar pelo Codex com MCP

O projeto inclui um servidor MCP Streamable HTTP em `mcp_server.py`, servido junto da interface web pelo `run.py`. A configuração em `.codex/config.toml` aponta o Codex para `http://127.0.0.1:5000/mcp`. Inicie a aplicação, abra esta pasta como projeto confiável e reinicie o Codex para carregar o servidor `novosaldo`. Use `/mcp` ou `codex mcp list` para confirmar a conexão.

O servidor compartilha `instance/financeiro.db` com a interface web e oferece ferramentas para:

- consultar contas, lançamentos, recorrências, resumo mensal e projeção;
- atualizar saldos, cadastrar lançamentos únicos/fixos e parcelamentos;
- dar baixa ou reabrir lançamentos e ativar/pausar recorrências;
- analisar uma fatura `.xlsx` antes da gravação e importá-la sem duplicar compras.

Para iniciar a aplicação web e o MCP HTTP juntos:

```powershell
uv run python run.py
```

A interface fica em `http://127.0.0.1:5000` e o endpoint MCP em `http://127.0.0.1:5000/mcp`.

O MCP exige um token privado em todas as requisições, inclusive locais. Configure `NOVOSALDO_MCP_TOKEN` no `.env`; sem ele, o endpoint retorna 503. Clientes devem enviar `Authorization: Bearer <token>`; credenciais ausentes ou inválidas retornam 401. O login Google e a dispensa de login local não substituem esse token.

A configuração do Codex usa `bearer_token_env_var = "NOVOSALDO_MCP_TOKEN"`. Disponibilize a mesma variável no ambiente do processo Codex antes de iniciá-lo e reinicie o cliente após configurá-la. Guarde o token fora do Git. Ao expor o MCP em outra máquina, use HTTPS.

Referência: [configuração MCP do Codex](https://developers.openai.com/codex/mcp/).

Para executar apenas o MCP, sem a interface web, use `uv run python mcp_server.py`; nesse modo, ele fica disponível em `http://127.0.0.1:8000/mcp`.

## Estrutura da aplicação

- `app.py`: camada web Flask (autenticação, rotas, mensagens e templates);
- `services/`: regras de negócio e coordenação dos casos de uso financeiros;
- `repositories/`: consultas e escritas das entidades no SQLite;
- `database.py`: conexão, schema e inicialização do banco de dados.

## Regras

- O saldo de fim de mês é: saldos bancários informados + receitas pendentes do mês − despesas pendentes do mês.
- Dar baixa não altera os saldos das contas automaticamente: informe os saldos reais na tela **Contas** para evitar dupla contagem.
- A projeção de 12 meses usa exclusivamente lançamentos fixos ativos.
- Na tela **Cartões**, é possível cadastrar cartões Itaú ou Bradesco, definir o dia de pagamento e consultar os lançamentos por cartão e mês da fatura.
- As compras de cartão entram como despesas no dia de pagamento cadastrado, dentro do mês de vencimento da fatura.
- Faturas do Itaú em `.xlsx` podem ser reenviadas enquanto estão abertas: lançamentos já existentes naquele cartão e mês são ignorados, e somente as novas compras são incluídas. Pagamentos, créditos e linhas inválidas também são ignorados.
- Ao importar uma compra parcelada, as parcelas restantes são previstas automaticamente nas próximas faturas. Quando uma dessas faturas for importada, a previsão é confirmada ou atualizada sem duplicar a despesa.
- O cadastro e os lançamentos manuais do Bradesco já estão disponíveis; a importação da fatura do Bradesco ficou preparada para uma implementação futura específica.


## Cloudflare Workers + D1

A aplicação também pode ser publicada como Python Worker, usando o banco D1
`personal-financer`. Consulte [o guia de publicação](cloudflare/README.md) para
configurar o banco existente, migrar dados locais, cadastrar os segredos Google e
publicar no Worker `personal-financer`.
