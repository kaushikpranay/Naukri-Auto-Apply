@echo off
echo ==================================================
echo Generating and Publishing static Naukri Dashboard...
echo ==================================================

python generate_dashboard.py
if %ERRORLEVEL% neq 0 (
    echo Error generating dashboard! Exiting...
    exit /b %ERRORLEVEL%
)

echo Committing and pushing docs/ [Naukri-Automation repo]...
git add docs
git commit -m "Update static dashboard data [automated]"
git push
if %ERRORLEVEL% neq 0 (
    echo Error pushing docs to remote repository!
    exit /b %ERRORLEVEL%
)

echo Committing and pushing dashboard data & code [parent repo]...
git -C .. add dashboard/public/data dashboard/src dashboard/backend
git -C .. commit -m "Update static dashboard data & code [automated]"
git -C .. push
if %ERRORLEVEL% neq 0 (
    echo Error pushing dashboard data to remote repository!
    exit /b %ERRORLEVEL%
)

echo Dashboard successfully published!
