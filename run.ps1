# AIHawk in one command: installs uv if it is missing, brings AIHawk up to date, and starts it.
if (-not (Get-Command uvx -ErrorAction SilentlyContinue)) {
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
# Python 3.14: on 3.15, the newest, PyYAML has no Windows build yet, and the install stopped asking for a C++ compiler.
uvx --python 3.14 --refresh-package aihawk --from git+https://github.com/feder-cr/Jobs_Applier_AI_Agent aihawk
