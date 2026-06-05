@echo off
echo ==================================================
echo Generating and Publishing static Naukri Dashboard...
echo ==================================================

python generate_dashboard.py
if %ERRORLEVEL% neq 0 (
    echo Error generating dashboard! Exiting...
    exit /b %ERRORLEVEL%
)

echo Staging docs/ files...
git add docs

echo Committing changes...
git commit -m "Update static dashboard data [automated]"

echo Pushing to GitHub...
git push
if %ERRORLEVEL% neq 0 (
    echo Error pushing to remote repository!
    exit /b %ERRORLEVEL%
)

echo Dashboard successfully published!
