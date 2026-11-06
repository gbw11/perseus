SUMMARY = "Playwright Python and JavaScript driver for the installed Chromium"
HOMEPAGE = "https://playwright.dev/python/"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://playwright-1.62.0.dist-info/licenses/LICENSE;md5=c1b091cb17bd885d46640c60d7034b85"
SRC_URI = "https://files.pythonhosted.org/packages/36/43/b4b18bdc87e1949568fffdcde3ff9a0456266b2d0c6d4432cc34d89ea6eb/playwright-1.62.0-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.whl;downloadfilename=playwright-${PV}.whl;unpack=0"
SRC_URI[sha256sum] = "db2d76613a57ad844362ce42f7d0c2fa26b19a4f7a46d4f76b891c631e6e5aff"
S = "${UNPACKDIR}/playwright-${PV}"

inherit allarch python3-dir

# The official wheel supplies Python/JS only; OE owns the Node executable and license.
RDEPENDS:${PN} = "python3-core python3-asyncio python3-json python3-logging python3-netclient python3-compression python3-pyee python3-greenlet nodejs"

python do_unpack:append() {
    import zipfile
    with zipfile.ZipFile(d.getVar("DL_DIR") + "/playwright-" + d.getVar("PV") + ".whl") as archive:
        archive.extractall(d.getVar("S"))
}

do_install() {
    install -d "${D}${PYTHON_SITEPACKAGES_DIR}"
    cd "${S}"
    find playwright -type f ! -path 'playwright/driver/node' | while IFS= read -r asset; do
        install -D -m 0644 "$asset" "${D}${PYTHON_SITEPACKAGES_DIR}/$asset"
    done
    cp -R playwright-${PV}.dist-info "${D}${PYTHON_SITEPACKAGES_DIR}/"
    ln -s "${bindir}/node" "${D}${PYTHON_SITEPACKAGES_DIR}/playwright/driver/node"
}

FILES:${PN} += "${PYTHON_SITEPACKAGES_DIR}"
