"""As placas de vídeo do computador (NVIDIA, AMD, Intel e as outras), integradas ou dedicadas, em Windows, Linux e Mac.

Isto só RECONHECE: quem decide onde a transcrição roda é o echo_ai.hardware (hoje, só a NVIDIA acelera). A leitura do
sistema passa por dois contratos pequenos, para os testes serem iguais em qualquer PC: `Executor` (rodar um programa e
ler a saída) e `Sistema` (qual sistema é este e ler arquivos/pastas). Cada sistema tem seu detector:

- Windows: `Get-CimInstance Win32_VideoController` pelo PowerShell (sem administrador). O campo AdapterRAM do Windows
  trava em 4 GB (limite de 32 bits), então a memória certa vem do registro (HardwareInformation.qwMemorySize), na
  mesma consulta; se a placa não estiver lá e o AdapterRAM estiver no teto, a memória fica desconhecida.
- Linux: /sys/class/drm (fabricante, modelo em número, memória de vídeo das AMD) junto com o `lspci -mm -nn` (o nome).
  Cada fonte funciona sem a outra (contêiner sem lspci, ou sem /sys).
- Mac: `system_profiler SPDisplaysDataType -json`.

Integrada ou dedicada é uma heurística pelo nome (em `e_integrada`); no Mac, o barramento que o sistema informa.
"""

import json
import logging
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

log = logging.getLogger("echo")

GB = 1024**3
TEMPO_DA_CONSULTA_S = 10.0  # o PowerShell frio leva ~1 s; passar disso é um sistema travado, e a CPU resolve
TETO_DO_ADAPTER_RAM = 0xFFF00000  # o Windows guarda a memória em 32 bits: perto de 4 GB é o teto, não o valor real
CLASSES_DE_VIDEO = ("0300", "0302", "0380")  # VGA, controlador 3D (placas de notebook) e outro controlador de vídeo
DRM = "/sys/class/drm"
CODIGO_DO_FABRICANTE = {"10de": "nvidia", "1002": "amd", "8086": "intel"}  # os códigos de fabricante do PCI

SCRIPT_DO_WINDOWS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$placas = @(Get-CimInstance Win32_VideoController | ForEach-Object { [pscustomobject]@{ nome = $_.Name; fabricante = $_.AdapterCompatibility; ram = $_.AdapterRAM; id = $_.PNPDeviceID } })
$memorias = @(Get-ChildItem -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Video' | ForEach-Object { Get-ChildItem -LiteralPath $_.PSPath } | ForEach-Object {
  $p = Get-ItemProperty -LiteralPath $_.PSPath
  $b = $p.'HardwareInformation.AdapterString'
  if ($b -is [byte[]] -and $p.'HardwareInformation.qwMemorySize') { [pscustomobject]@{ nome = [Text.Encoding]::Unicode.GetString($b).TrimEnd([char]0); bytes = [uint64]$p.'HardwareInformation.qwMemorySize' } }
})
ConvertTo-Json -Compress -Depth 4 -InputObject ([pscustomobject]@{ placas = $placas; memorias = $memorias })
"""  # noqa: E501


class Fabricante(StrEnum):
    NVIDIA = "nvidia"
    AMD = "amd"
    INTEL = "intel"
    OUTRO = "outro"


@dataclass(frozen=True)
class Gpu:
    fabricante: Fabricante
    nome: str
    memoria_gb: float | None  # memória de vídeo; None quando o sistema não informa
    integrada: bool  # divide a memória com o processador (heurística: veja e_integrada)

    def para_a_tela(self) -> dict[str, object]:
        return {
            "fabricante": self.fabricante.value,
            "nome": self.nome,
            "memoria_gb": round(self.memoria_gb, 1) if self.memoria_gb else None,
            "integrada": self.integrada,
        }


class Executor(Protocol):
    """Roda um programa e devolve o que ele escreveu; None se não existe, falhou ou passou do tempo."""

    def executar(self, comando: Sequence[str], tempo_s: float) -> str | None: ...


class Sistema(Protocol):
    """O sistema operacional visto pelo detector: qual é, e leitura de arquivos e pastas (caminhos no estilo do Linux)."""

    @property
    def nome(self) -> str: ...  # windows | mac | linux

    def ler_texto(self, caminho: str) -> str | None: ...

    def listar(self, caminho: str) -> list[str]: ...


class ExecutorReal:
    def executar(self, comando: Sequence[str], tempo_s: float) -> str | None:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no Windows, sem piscar uma janela de console
        try:
            resultado = subprocess.run(
                list(comando), capture_output=True, text=True, timeout=tempo_s, check=False, creationflags=flags
            )
        except (subprocess.SubprocessError, OSError) as erro:
            log.info("%s não respondeu: %s", comando[0], erro)
            return None
        if resultado.returncode != 0:
            log.info("%s terminou com código %d", comando[0], resultado.returncode)
            return None
        return resultado.stdout


class SistemaReal:
    @property
    def nome(self) -> str:
        return {"win32": "windows", "darwin": "mac"}.get(sys.platform, "linux")

    def ler_texto(self, caminho: str) -> str | None:
        try:
            return Path(caminho).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

    def listar(self, caminho: str) -> list[str]:
        try:
            return sorted(os.listdir(caminho))
        except OSError:
            return []


def limpar_nome(nome: str) -> str:
    """Tira (R) e (TM) e os espaços repetidos: "AMD Radeon(TM) Graphics" vira "AMD Radeon Graphics"."""
    return re.sub(r"\s+", " ", re.sub(r"\((?:R|TM)\)", "", nome, flags=re.IGNORECASE)).strip()


def fabricante_do_texto(texto: str) -> Fabricante:
    minusculo = texto.lower()
    if "nvidia" in minusculo:
        return Fabricante.NVIDIA
    if "advanced micro devices" in minusculo or "amd" in minusculo or re.search(r"\bati\b", minusculo) or "radeon" in minusculo:
        return Fabricante.AMD
    if "intel" in minusculo:
        return Fabricante.INTEL
    return Fabricante.OUTRO


def _virtual(nome: str) -> bool:
    """Adaptadores que não são placa: o vídeo básico da Microsoft, a área de trabalho remota, monitores virtuais."""
    minusculo = nome.lower()
    return any(
        marca in minusculo
        for marca in ("microsoft basic", "microsoft remote", "microsoft hyper-v", "remote display", "virtual display", "parsec")
    )


_AMD_DEDICADA = re.compile(
    r"\bRX \d{3,4}|\bRX Vega (?:56|64)\b|\bPro (?:W|WX|V|SSG)|\bWX \d|Instinct|FirePro|Radeon VII\b|\bNavi \d", re.IGNORECASE
)
_AMD_INTEGRADA = re.compile(r"\bGraphics\b|\b\d{3,4}[MS]\b|Vega (?:Series|Mobile)", re.IGNORECASE)
# os codinomes das APUs (Ryzen com vídeo); o lspci do Linux, às vezes, só traz o codinome
_AMD_APU = re.compile(
    r"Raven|Picasso|Renoir|Cezanne|Barcelo|Lucienne|Rembrandt|Phoenix|Hawk Point|Mendocino|Raphael|Strix|Kaveri|Carrizo|Stoney",
    re.IGNORECASE,
)
_INTEL_DEDICADA = re.compile(r"\bArc (?:Pro )?[AB]\d{2,3}|\bDG[12]\b|\bBMG\b|Data Center GPU|\bFlex \d|Max Series", re.IGNORECASE)


def e_integrada(fabricante: Fabricante, *textos: str) -> bool:
    """Heurística pelo nome (e pelo texto bruto do sistema, se houver). Integrada = divide a memória com o processador.

    - NVIDIA: dedicada (as MX e as de notebook também são dedicadas), menos as da linha Tegra/Orin (módulos embarcados).
    - AMD: dedicada se for "RX ####", Pro W/WX, Instinct, FirePro, Radeon VII ou Navi; integrada se for "Radeon Graphics",
      "Vega N Graphics", "###M/###S" (Radeon 780M, 8060S) ou codinome de APU; sem pista, dedicada.
    - Intel: dedicada só se for Arc com modelo (A380, B580...), Arc Pro, DG1/DG2, Flex ou Max; o resto (UHD, Iris, o "Arc
      Graphics" dos Core Ultra) é integrado.
    - Outras: dedicada (o Mac, que tem barramento no sistema, não passa por aqui)."""
    texto = " ".join(textos)
    if fabricante is Fabricante.NVIDIA:
        return bool(re.search(r"Tegra|Orin|Xavier", texto, re.IGNORECASE))
    if fabricante is Fabricante.AMD:
        if _AMD_DEDICADA.search(texto) and not re.search(r"Vega \d+ Graphics", texto, re.IGNORECASE):
            return False
        return bool(_AMD_INTEGRADA.search(texto) or _AMD_APU.search(texto))
    if fabricante is Fabricante.INTEL:
        return not _INTEL_DEDICADA.search(texto)
    return False


def _gb(valor: object) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, int | float) or valor <= 0:
        return None
    return float(valor) / GB


class DetectorDoWindows:
    def __init__(self, executor: Executor) -> None:
        self._executor = executor

    def detectar(self) -> list[Gpu]:
        saida = self._executor.executar(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", SCRIPT_DO_WINDOWS], TEMPO_DA_CONSULTA_S
        )
        if not saida or not saida.strip():
            return []
        try:
            dados = json.loads(saida)
        except ValueError as erro:
            log.warning("a lista de placas do Windows não é JSON: %s", erro)
            return []
        if not isinstance(dados, dict):
            return []
        memorias: dict[str, int] = {}
        for item in _lista(dados.get("memorias")):
            nome, bytes_ = item.get("nome"), item.get("bytes")
            if isinstance(nome, str) and isinstance(bytes_, int):  # várias entradas por placa: vale a maior
                memorias[limpar_nome(nome).casefold()] = max(bytes_, memorias.get(limpar_nome(nome).casefold(), 0))
        placas = []
        for item in _lista(dados.get("placas")):
            nome = item.get("nome")
            if not isinstance(nome, str) or not nome.strip() or _virtual(nome):
                continue
            nome = limpar_nome(nome)
            fabricante = fabricante_do_texto(str(item.get("fabricante") or ""))
            if fabricante is Fabricante.OUTRO:
                fabricante = fabricante_do_texto(nome)
            if fabricante is Fabricante.OUTRO:
                codigo = re.search(r"VEN_([0-9A-F]{4})", str(item.get("id") or ""), re.IGNORECASE)
                fabricante = Fabricante(CODIGO_DO_FABRICANTE.get(codigo.group(1).lower(), "outro")) if codigo else fabricante
            placas.append(Gpu(fabricante, nome, self._memoria(nome, item.get("ram"), memorias), e_integrada(fabricante, nome)))
        return placas

    @staticmethod
    def _memoria(nome: str, ram: object, memorias: dict[str, int]) -> float | None:
        do_registro = memorias.get(nome.casefold())
        if do_registro:
            return do_registro / GB
        if isinstance(ram, int) and not isinstance(ram, bool) and 0 < ram < TETO_DO_ADAPTER_RAM:
            return ram / GB
        return None  # no teto de 32 bits (4 GB ou mais de verdade) e sem o registro: melhor não chutar


def _lista(valor: object) -> list[dict[str, object]]:
    """O JSON do PowerShell traz um objeto solto quando há um só item: aceita os dois jeitos."""
    if isinstance(valor, dict):
        return [valor]
    if isinstance(valor, list):
        return [item for item in valor if isinstance(item, dict)]
    return []


@dataclass
class _PlacaDoPci:
    """O que se sabe de uma placa no Linux, juntando o lspci e o /sys pelo endereço PCI (ex.: 0000:03:00.0)."""

    endereco: str
    fabricante: Fabricante = Fabricante.OUTRO
    nome: str = ""
    texto_bruto: str = ""
    codigo_do_modelo: str = ""
    memoria_bytes: int | None = None


class DetectorDoLinux:
    def __init__(self, sistema: Sistema, executor: Executor) -> None:
        self._sistema = sistema
        self._executor = executor

    def detectar(self) -> list[Gpu]:
        placas: dict[str, _PlacaDoPci] = {}
        for item in self._do_lspci():
            placas[item.endereco] = item
        for item in self._do_sys():
            existente = placas.get(item.endereco) or placas.get(item.endereco.removeprefix("0000:"))
            if existente is None:
                placas[item.endereco] = item
                continue
            existente.memoria_bytes = item.memoria_bytes or existente.memoria_bytes
            if existente.fabricante is Fabricante.OUTRO:
                existente.fabricante = item.fabricante
        resultado = []
        for p in placas.values():
            nome = p.nome or f"{self._marca(p.fabricante)} (PCI {p.codigo_do_modelo or '?'})"
            resultado.append(
                Gpu(
                    p.fabricante,
                    nome,
                    p.memoria_bytes / GB if p.memoria_bytes else None,
                    e_integrada(p.fabricante, nome, p.texto_bruto),
                )
            )
        return resultado

    @staticmethod
    def _marca(fabricante: Fabricante) -> str:
        return {Fabricante.NVIDIA: "NVIDIA", Fabricante.AMD: "AMD", Fabricante.INTEL: "Intel"}.get(fabricante, "Placa de vídeo")

    def _do_lspci(self) -> list[_PlacaDoPci]:
        saida = self._executor.executar(["lspci", "-mm", "-nn"], TEMPO_DA_CONSULTA_S)
        achadas = []
        for linha in (saida or "").splitlines():
            try:
                campos = shlex.split(linha)
            except ValueError:
                continue
            if len(campos) < 4:
                continue
            classe = re.search(r"\[([0-9a-f]{4})\]$", campos[1])
            if not classe or classe.group(1) not in CLASSES_DE_VIDEO:
                continue
            fabricante = fabricante_do_texto(re.sub(r"\[[0-9a-f]{4}\]$", "", campos[2]))
            if fabricante is Fabricante.OUTRO:
                codigo = re.search(r"\[([0-9a-f]{4})\]$", campos[2])
                fabricante = Fabricante(CODIGO_DO_FABRICANTE.get(codigo.group(1), "outro")) if codigo else fabricante
            bruto = re.sub(r"\s*\[[0-9a-f]{4}\]$", "", campos[3])  # "Navi 23 [Radeon RX 6600/6600 XT/6600M]"
            comercial = re.search(r"\[(.+)\]$", bruto)
            modelo = (comercial.group(1) if comercial else bruto).split("/")[0].strip()
            marca = self._marca(fabricante)
            nome = limpar_nome(modelo if modelo.lower().startswith(marca.lower()) else f"{marca} {modelo}")
            achadas.append(_PlacaDoPci(campos[0], fabricante, nome, bruto))
        return achadas

    def _do_sys(self) -> list[_PlacaDoPci]:
        achadas = []
        for entrada in self._sistema.listar(DRM):
            if not re.fullmatch(r"card\d+", entrada):  # card0-DP-1 e renderD128 não são placas
                continue
            base = f"{DRM}/{entrada}/device"
            classe = (self._sistema.ler_texto(f"{base}/class") or "").strip().lower()
            if not classe.startswith("0x03"):
                continue
            codigo = (self._sistema.ler_texto(f"{base}/vendor") or "").strip().lower().removeprefix("0x")
            modelo = (self._sistema.ler_texto(f"{base}/device") or "").strip().lower().removeprefix("0x")
            endereco = re.search(r"PCI_SLOT_NAME=(\S+)", self._sistema.ler_texto(f"{base}/uevent") or "")
            memoria = (self._sistema.ler_texto(f"{base}/mem_info_vram_total") or "").strip()  # só o driver amdgpu informa
            achadas.append(
                _PlacaDoPci(
                    endereco=endereco.group(1) if endereco else entrada,
                    fabricante=Fabricante(CODIGO_DO_FABRICANTE.get(codigo, "outro")),
                    codigo_do_modelo=modelo,
                    memoria_bytes=int(memoria) if memoria.isdigit() else None,
                )
            )
        return achadas


class DetectorDoMac:
    def __init__(self, executor: Executor) -> None:
        self._executor = executor

    def detectar(self) -> list[Gpu]:
        saida = self._executor.executar(["system_profiler", "SPDisplaysDataType", "-json"], TEMPO_DA_CONSULTA_S)
        if not saida:
            return []
        try:
            dados = json.loads(saida)
        except ValueError as erro:
            log.warning("a lista de placas do Mac não é JSON: %s", erro)
            return []
        itens = _lista(dados.get("SPDisplaysDataType")) if isinstance(dados, dict) else []
        placas = []
        for item in itens:
            nome = item.get("sppci_model") or item.get("_name")
            if not isinstance(nome, str) or not nome.strip():
                continue
            nome = limpar_nome(nome)
            vendedor = str(item.get("spdisplays_vendor") or "")
            fabricante = fabricante_do_texto(vendedor.replace("sppci_vendor_", ""))
            if fabricante is Fabricante.OUTRO:
                fabricante = fabricante_do_texto(nome)
            barramento = str(item.get("sppci_bus") or "")
            if "builtin" in barramento or "apple" in vendedor.lower():
                integrada = True
            elif "pcie" in barramento or "egpu" in barramento:
                integrada = False
            else:
                integrada = e_integrada(fabricante, nome)
            placas.append(Gpu(fabricante, nome, _memoria_do_mac(str(item.get("spdisplays_vram") or "")), integrada))
        return placas


def _memoria_do_mac(texto: str) -> float | None:
    """ "8 GB" ou "1536 MB" -> GB. O Mac com chip Apple tem memória unificada e não informa: None."""
    achado = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(GB|MB)\s*", texto, re.IGNORECASE)
    if not achado:
        return None
    valor = float(achado.group(1).replace(",", "."))
    return valor if achado.group(2).upper() == "GB" else valor / 1024


class DetectorDeGpus:
    """Todas as placas de vídeo do computador, pelo jeito de cada sistema. Nunca levanta erro: sem resposta, lista vazia."""

    def __init__(self, sistema: Sistema | None = None, executor: Executor | None = None) -> None:
        self._sistema: Sistema = sistema or SistemaReal()
        self._executor: Executor = executor or ExecutorReal()

    def detectar(self) -> tuple[Gpu, ...]:
        try:
            gpus = self._detector().detectar()
        except Exception:  # fronteira com o sistema: uma saída esquisita não pode derrubar a abertura do app
            log.exception("não consegui listar as placas de vídeo")
            return ()
        return tuple(gpus)

    def _detector(self) -> "DetectorDoWindows | DetectorDoLinux | DetectorDoMac":
        match self._sistema.nome:
            case "windows":
                return DetectorDoWindows(self._executor)
            case "mac":
                return DetectorDoMac(self._executor)
            case _:
                return DetectorDoLinux(self._sistema, self._executor)


def principal(gpus: Sequence[Gpu]) -> Gpu | None:
    """A placa mais capaz para o texto da tela: dedicada antes de integrada, depois a de mais memória, depois a NVIDIA."""
    ordem = {Fabricante.NVIDIA: 0, Fabricante.AMD: 1, Fabricante.INTEL: 2, Fabricante.OUTRO: 3}
    return min(gpus, key=lambda g: (g.integrada, -(g.memoria_gb or 0.0), ordem[g.fabricante]), default=None)
