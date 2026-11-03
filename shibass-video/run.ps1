param([switch]$Redo)
# ShiBass lesson videos - one command. Downloads make-video.py, then: audio in your voice -> videos. ASCII-only.
$py  = "C:\venvs\chatterbox\Scripts\python.exe"
$dir = "C:\ShiBass_Voice\courses\groove"; $aud = "${dir}_audio"; $scr = "${dir}_screen"; $vid = "${dir}_video"
$url = "https://raw.githubusercontent.com/shibass792/code-tracking/claude/modest-babbage-7807qf/shibass-video/make-video.py"
if (-not (Test-Path $py)) { Write-Host "Python env missing: $py" -ForegroundColor Red; return }
New-Item -ItemType Directory -Force $aud,$scr,$vid | Out-Null
Write-Host "[1/4] Downloading video maker" -ForegroundColor Cyan
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest $url -OutFile C:\ShiBass_Voice\make-video.py -UseBasicParsing
if (-not (Test-Path C:\ShiBass_Voice\make-video.py)) { Write-Host "Download failed" -ForegroundColor Red; return }
& $py -m pip install --quiet --no-deps python-bidi pymupdf
# Brand images: download them from Google Drive (one by one) - this picks them up from Downloads
$dl = Join-Path $HOME "Downloads"
$pick = @(@("*14.07.55 b*.png", "avatar.png"), @("shaulshelach*.png", "portrait.png"), @("ShiBass*.pdf", "logo.pdf"))
foreach ($p in $pick) {
  $f = Get-ChildItem $dl -Filter $p[0] -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if ($f) {
    Copy-Item $f.FullName ("C:\ShiBass_Voice\" + $p[1]) -Force
    Write-Host ("  found " + $f.Name + " -> " + $p[1]) -ForegroundColor Green
    if ($p[1] -eq "avatar.png") { Set-Content -Encoding ASCII C:\ShiBass_Voice\avatar.json '{"mouth_frac":[0.416,0.4198,0.4892]}' }
  } elseif (-not (Test-Path ("C:\ShiBass_Voice\" + $p[1]))) { Write-Host ("  not in Downloads: " + $p[0]) -ForegroundColor Yellow }
}
Write-Host "[2/4] Lessons" -ForegroundColor Cyan
$n = (Get-ChildItem $dir -Filter *.txt -ErrorAction SilentlyContinue).Count
Write-Host "  $n lessons in $dir"
if ($n -eq 0) { Write-Host "No lessons found - send this output to Claude" -ForegroundColor Red; return }
Write-Host "[3/4] Audio in your voice (skips existing)" -ForegroundColor Cyan
$v = @("C:\ShiBass_Voice\whatsapp.ogg","C:\ShiBass_Voice\voice-B.flac","C:\ShiBass_Voice\my-voice.flac","C:\ShiBass_Voice\my-voice.wav") | ? { Test-Path $_ } | Select-Object -First 1
if ($v -and (Test-Path C:\ShiBass_Voice\he-batch.py)) { & $py C:\ShiBass_Voice\he-batch.py $v --in $dir --out $aud --base male "--rate=-5%" --pause 0.15 }
else { Write-Host "  voice file or he-batch.py missing - using existing audio only" -ForegroundColor Yellow }
Write-Host "[4/4] Videos" -ForegroundColor Cyan
if ($Redo) { & $py C:\ShiBass_Voice\make-video.py --course $dir --redo } else { & $py C:\ShiBass_Voice\make-video.py --course $dir }
Write-Host "DONE. Videos: $vid" -ForegroundColor Green
explorer $vid
