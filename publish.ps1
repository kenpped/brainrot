# publish.ps1 -- put brainrot studio on the public internet, free.
# Prereq (one time): gh auth login
# Then: powershell -ExecutionPolicy Bypass -File publish.ps1
# Safe to rerun; every step skips itself if already done.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) { $gh = "C:\Program Files\GitHub CLI\gh.exe" }
if (-not (Test-Path $gh)) {
    Write-Host "ERROR: GitHub CLI not found. Reopen your terminal (fresh PATH) and retry."
    exit 1
}

& $gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: not logged in. Run:  gh auth login   (pick GitHub.com, browser login), then rerun this."
    exit 1
}
$user = (& $gh api user --jq .login).Trim()
Write-Host "logged in as $user"

# 1. bake the username into the landing page (idempotent)
$idx = "docs\index.html"
$html = Get-Content $idx -Raw
if ($html -match "\{\{GITHUB_USER\}\}") {
    $html -replace "\{\{GITHUB_USER\}\}", $user | Set-Content $idx -NoNewline -Encoding utf8
    git add $idx
    git -c user.name="Ken" -c user.email="sirkentavius@gmail.com" commit -m "fill github username into landing page"
    Write-Host "landing page personalized for $user"
}

# 2. create the public repo and push (or just push if it exists)
git remote get-url origin 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    & $gh repo create brainrot --public --source . --push
    Write-Host "repo created: https://github.com/$user/brainrot"
} else {
    git push -u origin master
    Write-Host "pushed to existing origin"
}

# 3. demo videos as release assets (binaries stay out of git history)
& $gh release view v0.1 -R "$user/brainrot" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    $demos = @("out\robot-demon.mp4", "out\dopamine-subway.mp4", "out\battery-winter.mp4") | Where-Object { Test-Path $_ }
    & $gh release create v0.1 @demos -R "$user/brainrot" --title "demo videos" --notes "Demo renders embedded on the landing page."
    Write-Host "demo videos uploaded ($($demos.Count))"
} else {
    Write-Host "release v0.1 already exists, skipping"
}

# 4. turn on GitHub Pages from master:/docs (free hosting)
& $gh api "repos/$user/brainrot/pages" -X POST -f "source[branch]=master" -f "source[path]=/docs" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    & $gh api "repos/$user/brainrot/pages" -X PUT -f "source[branch]=master" -f "source[path]=/docs" 2>$null | Out-Null
}

Write-Host ""
Write-Host "DONE. Your official site (first build takes a minute or two):"
Write-Host "    https://$user.github.io/brainrot/"
Write-Host "Repo: https://github.com/$user/brainrot"
