$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSCommandPath
$deck=Get-ChildItem -LiteralPath $root -Filter '*.pptx' | Where-Object { $_.BaseName -ne 'base' } | Select-Object -First 1
if (!$deck) { throw 'Editable deck missing' }
$preview=Join-Path $root 'previews'
[void](New-Item -ItemType Directory -Force -Path $preview)
$before=@(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue | ForEach-Object Id)
$app=$null;$presentation=$null;$owned=$false
$script:freeforms=0;$script:pictures=0;$script:firstFreeform=$null
function Inspect-Shape($shape) {
  if ([int]$shape.Type -eq 6) {
    for($i=1;$i -le $shape.GroupItems.Count;$i++){Inspect-Shape $shape.GroupItems.Item($i)}
  } elseif ([int]$shape.Type -eq 5) {
    $script:freeforms++
    if (!$script:firstFreeform) {$script:firstFreeform=$shape}
  } elseif ([int]$shape.Type -in @(11,13)) {$script:pictures++}
}
try {
  $app=New-Object -ComObject PowerPoint.Application
  $app.AutomationSecurity=3
  $newPids=@(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue | Where-Object { $_.Id -notin $before } | ForEach-Object Id)
  $owned=($before.Count -eq 0 -and $newPids.Count -eq 1)
  $presentation=$app.Presentations.Open($deck.FullName,-1,0,0)
  for($i=1;$i -le $presentation.Slides.Count;$i++) {
    $slide=$presentation.Slides.Item($i)
    $slide.Export((Join-Path $preview ('slide-'+$i+'.png')),'PNG',1536,1024)
    for($j=1;$j -le $slide.Shapes.Count;$j++){Inspect-Shape $slide.Shapes.Item($j)}
  }
  if ($script:freeforms -ne 42 -or $script:pictures -ne 0) { throw 'Logo did not remain native editable freeforms' }
  $shape=$script:firstFreeform
  $points=$shape.Nodes.Item(1).Points
  $a=$points.GetLowerBound(0);$b=$points.GetLowerBound(1)
  $x=[single]$points.GetValue($a,$b);$y=[single]$points.GetValue($a,$b+1)
  $shape.Nodes.SetPosition(1,($x+1),($y+1));$shape.Nodes.SetPosition(1,$x,$y)
  $old=$shape.Fill.ForeColor.RGB;$shape.Fill.ForeColor.RGB=255;$shape.Fill.ForeColor.RGB=$old
  $result=@{slides=$presentation.Slides.Count;nativeFreeforms=$script:freeforms;pictures=$script:pictures;pointEditing=$true;fillEditing=$true}
  $result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $preview 'verification.json') -Encoding UTF8
  $result | ConvertTo-Json
} finally {
  if($presentation){$presentation.Saved=-1;$presentation.Close();[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($presentation)}
  if($app){if($owned -and $app.Presentations.Count -eq 0){$app.Quit()};[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($app)}
}
