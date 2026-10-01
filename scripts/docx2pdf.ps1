# Конвертация .docx -> .pdf через MS Word (COM). Word должен быть установлен.
# Использование: powershell -File docx2pdf.ps1 -Docx "ЛР2\Отчет_ЛР2.docx" -Pdf "ЛР2\Отчет_ЛР2.pdf"
param(
    [Parameter(Mandatory = $true)][string]$Docx,
    [Parameter(Mandatory = $true)][string]$Pdf
)

$docxPath = (Resolve-Path -LiteralPath $Docx).Path
$pdfPath = [System.IO.Path]::GetFullPath($Pdf)

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Open($docxPath, $false, $true)   # ConfirmConversions, ReadOnly
    # wdExportFormatPDF = 17
    $doc.ExportAsFixedFormat($pdfPath, 17)
    $doc.Close($false)
    Write-Output "OK $pdfPath ($([math]::Round((Get-Item -LiteralPath $pdfPath).Length / 1KB)) KB)"
}
finally {
    $word.Quit()
    [void][System.Runtime.Interopservices.Marshal]::ReleaseComObject($word)
}
