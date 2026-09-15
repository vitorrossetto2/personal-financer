import asyncio
import os
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from openpyxl import Workbook


PROJECT_DIR = Path(__file__).resolve().parents[1]


def create_statement(path: Path):
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "Vencimento"
    sheet["A2"] = "01/09/2026"
    sheet.append([])
    sheet.append(["Data", "Lançamento", "Parcelamento", "Valor"])
    sheet.append(["13/08/2026", "Mercado MCP", "", 125.50])
    sheet.append([None, "Subtotal", None, "=SUBTOTAL(9,D5:D5)"])
    workbook.save(path)
    workbook.close()


class MCPServerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_tools_read_write_and_deduplicate_statement(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            statement_path = temp_dir / "fatura.xlsx"
            create_statement(statement_path)
            env = dict(os.environ)
            env["NOVOSALDO_DATABASE"] = str(temp_dir / "financeiro.db")
            with socket.socket() as available_socket:
                available_socket.bind(("127.0.0.1", 0))
                port = available_socket.getsockname()[1]
            env["NOVOSALDO_PORT"] = str(port)
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(PROJECT_DIR / "run.py"),
                cwd=PROJECT_DIR,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            base_url = f"http://127.0.0.1:{port}"
            try:
                for _ in range(100):
                    if process.returncode is not None:
                        error = (await process.stderr.read()).decode(errors="replace")
                        self.fail(f"Servidor HTTP encerrou durante a inicialização: {error}")
                    try:
                        await asyncio.to_thread(urllib.request.urlopen, base_url, timeout=1)
                        break
                    except Exception:
                        await asyncio.sleep(0.05)
                else:
                    self.fail("Servidor HTTP não iniciou dentro do prazo.")

                async with streamable_http_client(f"{base_url}/mcp") as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await self.exercise_tools(session, statement_path)
            finally:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()

    async def exercise_tools(self, session: ClientSession, statement_path: Path):
        initialized = await session.initialize()
        self.assertEqual(initialized.server_info.name, "novosaldo")

        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        self.assertEqual(len(names), 14)
        self.assertIn("resumo_mensal", names)
        self.assertIn("importar_fatura_cartao", names)
        self.assertIn("listar_cartoes", names)

        accounts = await session.call_tool("listar_contas", {})
        self.assertFalse(accounts.is_error)
        self.assertEqual(len(accounts.structured_content["contas"]), 2)

        created_card = await session.call_tool(
            "cadastrar_cartao",
            {"name": "Itaú MCP", "issuer": "itau", "last_four": "2788", "payment_day": 1},
        )
        self.assertFalse(created_card.is_error)
        card_id = created_card.structured_content["card_id"]

        created = await session.call_tool(
            "cadastrar_lancamento",
            {
                "kind": "expense",
                "description": "Teste MCP",
                "amount": 45.75,
                "due_date": "2026-09-05",
                "category": "Teste",
            },
        )
        self.assertFalse(created.is_error)
        transaction_id = created.structured_content["transaction_id"]

        settled = await session.call_tool(
            "definir_status_lancamento",
            {"transaction_id": transaction_id, "status": "settled"},
        )
        self.assertEqual(settled.structured_content["status"], "settled")

        analysis = await session.call_tool(
            "analisar_fatura_cartao", {"path": str(statement_path)}
        )
        self.assertEqual(analysis.structured_content["entries"], 1)
        self.assertEqual(analysis.structured_content["total"], 125.5)

        first_import = await session.call_tool(
            "importar_fatura_cartao", {"path": str(statement_path), "credit_card_id": card_id}
        )
        second_import = await session.call_tool(
            "importar_fatura_cartao", {"path": str(statement_path), "credit_card_id": card_id}
        )
        self.assertEqual(first_import.structured_content["imported"], 1)
        self.assertEqual(second_import.structured_content["imported"], 0)
        self.assertEqual(second_import.structured_content["duplicates"], 1)


if __name__ == "__main__":
    unittest.main()
