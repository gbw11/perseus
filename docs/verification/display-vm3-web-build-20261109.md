# VM3 Interaction 화면 프론트 빌드 검증 (2026-11-09)

## 대상

- 앱: `frontend/apps/vm-screens`
- 화면 경로: `/display/mobile`
- 소스 revision: `447d0d06dea1e0e27eafcbd0b80788c1fe97266f`
- 배포 대상: VM3 `/opt/xenova/display-web`

## 빌드 환경

- Node.js: `v24.18.0`
- npm: `11.16.0`
- Vite: `8.3.4`

## 실행 명령

```powershell
frontend\node_modules\.bin\tsc.cmd -p frontend\apps\vm-screens\tsconfig.json
frontend\node_modules\.bin\vite.cmd build frontend\apps\vm-screens
```

실제 실행은 `frontend/apps/vm-screens`를 작업 디렉터리로 두고 동일한 `tsc`와 `vite build`를 호출했다.

## 결과

- TypeScript 검사 성공
- Vite production build 성공
- 출력 파일: 8개
- 전체 크기: 595,710 bytes
- 산출물 tree SHA-256: `f23fb9f0881768d1599428dd524e95a30a74901a161ad1977b9c3a7922378fba`
- 생성된 JavaScript에서 `/display/mobile` 경로 확인

| 파일 | 크기(bytes) | SHA-256 |
| --- | ---: | --- |
| `index.html` | 501 | `c84198bb72baa8403ab20e58b69d5011146c74f29d1ecc994cf5c682e4bedff9` |
| `favicon.png` | 1,977 | `61f27411abef4809b83d2198d73f728091947246a18c86446d3c2373dd4a24d6` |
| `assets/index-CvxUBLSk.css` | 27,579 | `9136d76ea6921eb30fbc695a5faa8d55b242cd997796d790520d775f5c385331` |
| `assets/index-XuG1bRdc.js` | 479,967 | `76a4d660dc47a1a20eb0e0724737a3a715617e10e063b825e9c84470f6af3076` |
| `assets/MobilePage-CaN_vEhm.js` | 5,179 | `0cd4f31d3cfef07cba21bdc6aa5a8b01898f43cc7cdad283705e65ee9b45c351` |
| `assets/MobilePage-CUfO5ApN.css` | 6,275 | `23179e93f35926f2e3863e10539137be6dde2aae7a9f7fdd731335b7c92a945a` |
| `assets/xenova-logo-NjuEsEqP.png` | 60,276 | `0988cee695f0d507c25dac32a2ee0c4dfb85c52911576f3b28b8d398d095bb56` |
| `assets/xenova-mark-LxQ2B99p.png` | 13,956 | `055946ea9db0d3509f5ab0edfa1b1328895abc761e137b805aeff02fc47c6162` |

`dist` 자체는 저장소에 커밋하지 않는다. Debian 템플릿 빌드 시 `--display-web-assets frontend/apps/vm-screens/dist`와 `--display-web-revision 447d0d06dea1e0e27eafcbd0b80788c1fe97266f`를 전달해 VM 이미지에 설치한다.
