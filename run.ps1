# AIHawk in one command: installs uv if it is missing, brings AIHawk up to date, and starts it.

# AIHawk's code as an archive, not a clone of its repository: a clone needs git, which many computers do not have, and
# brings the whole history, 180 MB against 7.
$source = "aihawk @ https://github.com/feder-cr/Jobs_Applier_AI_Agent/archive/refs/heads/main.tar.gz"
# Python 3.14: on 3.15, the newest, PyYAML has no Windows build yet, and the install stopped asking for a C++ compiler.
$python = "3.14"

# uv installed by an earlier run is here, also in a window opened before it was.
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
if (-not (Get-Command uvx -ErrorAction SilentlyContinue)) {
    Write-Host "AIHawk: getting ready. The first start downloads about 250 MB and takes a minute or two; the next ones take seconds."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    if (-not (Get-Command uvx -ErrorAction SilentlyContinue)) {
        Write-Host "AIHawk could not be installed: check your connection and run this again."
        return
    }
}

# The newest AIHawk is fetched first, and then started from this computer's copy. Without a connection the copy already
# here starts as it is: AIHawk must not need GitHub to open.
uvx --python $python --refresh-package aihawk --from $source aihawk --version | Out-Null
if ($LASTEXITCODE -eq 0) {
    uvx --python $python --from $source aihawk
} else {
    Write-Host "AIHawk could not check for its newest version (no connection?). Starting the one on this computer."
    uvx --offline --python $python --from $source aihawk
}
