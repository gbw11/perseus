@ECHO OFF
SETLOCAL

SET "BASEDIR=%~dp0"
SET "BASEDIR=%BASEDIR:~0,-1%"
SET "WRAPPER_JAR=%BASEDIR%\.mvn\wrapper\maven-wrapper.jar"

IF NOT EXIST "%WRAPPER_JAR%" (
  ECHO Maven Wrapper JAR not found: %WRAPPER_JAR%
  ECHO Run the repository setup step or download Maven Wrapper 3.3.4.
  EXIT /B 1
)

java "-Dmaven.multiModuleProjectDirectory=%BASEDIR%" -classpath "%WRAPPER_JAR%" org.apache.maven.wrapper.MavenWrapperMain %*
EXIT /B %ERRORLEVEL%
