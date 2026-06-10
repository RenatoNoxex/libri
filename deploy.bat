@echo off
echo ============================================
echo  Deploy Libri su www.exmu.it/libri/
echo ============================================
echo.
echo Caricamento index.html...
curl.exe --ssl-reqd -k -u "1274854@aruba.it:4Ba34qaq!!" -T "index.html" ftp://ftp.exmu.it/www.exmu.it/libri/index.html
echo.
echo Caricamento dettaglio.html...
curl.exe --ssl-reqd -k -u "1274854@aruba.it:4Ba34qaq!!" -T "dettaglio.html" ftp://ftp.exmu.it/www.exmu.it/libri/dettaglio.html
echo.
echo Caricamento banner.jpg...
curl.exe --ssl-reqd -k -u "1274854@aruba.it:4Ba34qaq!!" -T "banner.jpg" ftp://ftp.exmu.it/www.exmu.it/libri/banner.jpg
echo.
echo ============================================
echo  Deploy completato! Verifica:
echo  https://www.exmu.it/libri/
echo ============================================
pause