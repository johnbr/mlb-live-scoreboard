# Changelog

## [1.21.0](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.20.2...v1.21.0) (2026-05-27)


### Features

* **card:** show pitch type + velocity for each pitch in the at-bat ([168a8f6](https://github.com/johnbr/mlb-live-scoreboard/commit/168a8f6d4a547893cc924209bc0bdf6fd697a3f5))

## [1.20.2](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.20.1...v1.20.2) (2026-05-27)


### Bug Fixes

* **coordinator:** suppress score events when prev baseline is empty ([0f6219e](https://github.com/johnbr/mlb-live-scoreboard/commit/0f6219e3c482b851db5bbd4eaf0bb3a11a3afe99))

## [1.20.1](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.20.0...v1.20.1) (2026-05-26)


### Bug Fixes

* **coordinator:** recognize ESPN's "S" save code in pitching decisions ([253ecfe](https://github.com/johnbr/mlb-live-scoreboard/commit/253ecfe011a4f3b1191362225245a0df2cac44a7))

## [1.20.0](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.19.3...v1.20.0) (2026-05-26)


### Features

* **card:** compact decisions cell + show save count in parens ([dd4506b](https://github.com/johnbr/mlb-live-scoreboard/commit/dd4506b8699c22e781c161772349ac8c8ddb6f74))

## [1.19.3](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.19.2...v1.19.3) (2026-05-24)


### Bug Fixes

* **coordinator:** dedupe scoring plays so duplicates don't render twice ([#46](https://github.com/johnbr/mlb-live-scoreboard/issues/46)) ([a40d1f1](https://github.com/johnbr/mlb-live-scoreboard/commit/a40d1f10b4d20cc7146aa65ab6b2fb778d91cf9d))

## [1.19.2](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.19.1...v1.19.2) (2026-05-24)


### Bug Fixes

* **card:** center and bolden sub-panel headings in final-game expand ([#44](https://github.com/johnbr/mlb-live-scoreboard/issues/44)) ([5886b1b](https://github.com/johnbr/mlb-live-scoreboard/commit/5886b1ba3f34d43cceb7d82df1b8c67026fe9451))

## [1.19.1](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.19.0...v1.19.1) (2026-05-24)


### Bug Fixes

* **card:** detect rain/weather/suspended delays, not just "Delayed" ([a0a35ee](https://github.com/johnbr/mlb-live-scoreboard/commit/a0a35eecbea24bb818d1b66d3473db65cb70bdb9))

## [1.19.0](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.18.0...v1.19.0) (2026-05-24)


### Features

* **card:** tighten and weight decisions panel meta + stat lines ([#40](https://github.com/johnbr/mlb-live-scoreboard/issues/40)) ([71591aa](https://github.com/johnbr/mlb-live-scoreboard/commit/71591aacee61a3322b11200ab56558c586db6fc7))

## [1.18.0](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.17.1...v1.18.0) (2026-05-24)


### Features

* **card:** per-pitcher game stats + flush layout in decisions panel ([f087117](https://github.com/johnbr/mlb-live-scoreboard/commit/f087117f68dae24bd2e077c659be1529597522e0))

## [1.17.1](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.17.0...v1.17.1) (2026-05-24)


### Bug Fixes

* **coordinator:** unstick inning context when ESPN status lags plays ([a799d9e](https://github.com/johnbr/mlb-live-scoreboard/commit/a799d9e85083a1d7f3915cbcf40e53544c0d8c85))

## [1.17.0](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.16.2...v1.17.0) (2026-05-24)


### Features

* **card:** add pitcher decisions panel (W/L/SV) to final-game summary ([c95a66f](https://github.com/johnbr/mlb-live-scoreboard/commit/c95a66f00390b6ceef8a87861640870a20a8e343))

## [1.16.2](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.16.1...v1.16.2) (2026-05-23)


### Bug Fixes

* **card:** render postponed games as PPD instead of Final 0-0 ([7bce2e5](https://github.com/johnbr/mlb-live-scoreboard/commit/7bce2e5d2bd850ab08f2dd584c1562b6cd6aa17a))

## [1.16.1](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.16.0...v1.16.1) (2026-05-23)


### Bug Fixes

* **coordinator:** don't render postponed game as 0-0 final ([fa9e3ef](https://github.com/johnbr/mlb-live-scoreboard/commit/fa9e3efe8990ce5ad60160284e489bb2e827515d))

## [1.16.0](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.15.3...v1.16.0) (2026-05-22)


### Features

* **card:** batter-line polish, post-final record sync, highlights link ([3352c1b](https://github.com/johnbr/mlb-live-scoreboard/commit/3352c1beba122e9ed4761025b05d0327d17e4e9e))

## [1.15.3](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.15.2...v1.15.3) (2026-05-20)


### Bug Fixes

* **coordinator:** show two-way players' real batting line at the plate ([1a3831a](https://github.com/johnbr/mlb-live-scoreboard/commit/1a3831af9c2802751981847b5f3698a185ba8eb1))

## [1.15.2](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.15.1...v1.15.2) (2026-05-20)


### Bug Fixes

* **docs:** absolute URLs for README screenshots so HACS renders them ([3de8aeb](https://github.com/johnbr/mlb-live-scoreboard/commit/3de8aeb8e64754e0507da1a2dcbb30cab43cdfe6))

## [1.15.1](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.15.0...v1.15.1) (2026-05-20)


### Bug Fixes

* **docs:** add screenshots ([fa91867](https://github.com/johnbr/mlb-live-scoreboard/commit/fa91867570d19c9945141348b0762d6d8bedf841))

## [1.15.0](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.14.1...v1.15.0) (2026-05-20)


### Features

* **card:** responsive headshots + headshot_size option ([fb8031b](https://github.com/johnbr/mlb-live-scoreboard/commit/fb8031b0a9679bec98fd7f71fcf41118a9cab159))

## [1.14.1](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.14.0...v1.14.1) (2026-05-20)


### Bug Fixes

* **card:** wrap scoring-play text instead of truncating ([b563716](https://github.com/johnbr/mlb-live-scoreboard/commit/b563716dc005a5ee8c0b13b6d37f7682f199e28d))

## [1.14.0](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.13.0...v1.14.0) (2026-05-20)


### Features

* **card:** post-game summary in final-game expand panel ([3821013](https://github.com/johnbr/mlb-live-scoreboard/commit/38210135681aaad1dee4aa70923bd5fdde26f3c5))

## [1.13.0](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.12.0...v1.13.0) (2026-05-19)


### Features

* **card:** visual editor and UI-only install/configure ([289a15e](https://github.com/johnbr/mlb-live-scoreboard/commit/289a15e85fb25810f237aa87410047f24a6058c9))

## [1.12.0](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.11.0...v1.12.0) (2026-05-19)


### Features

* **lineup:** team lineup popup with Game/Season stats ([fdf309b](https://github.com/johnbr/mlb-live-scoreboard/commit/fdf309b127e76a05a749eaa13baf888725ac92c3))

## [1.11.0](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.10.0...v1.11.0) (2026-05-18)


### Features

* in-card player career stats popup ([#10](https://github.com/johnbr/mlb-live-scoreboard/issues/10)) ([ea4657b](https://github.com/johnbr/mlb-live-scoreboard/commit/ea4657bb9d4e74e40905dcb2c3b65feb6eceac87))

## [1.10.0](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.9.3...v1.10.0) (2026-05-17)


### Features

* **card:** link player names to ESPN player pages ([7a1786c](https://github.com/johnbr/mlb-live-scoreboard/commit/7a1786c9a15f3b25f3ddbb4665d064a41dc94b59))

## [1.9.3](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.9.2...v1.9.3) (2026-05-15)


### Bug Fixes

* **winprob:** keep both team labels readable at lopsided percentages ([#6](https://github.com/johnbr/mlb-live-scoreboard/issues/6)) ([a4c9fff](https://github.com/johnbr/mlb-live-scoreboard/commit/a4c9ffffb64b0416e8ab934130fac5125d06a220))

## [1.9.2](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.9.1...v1.9.2) (2026-05-15)


### Bug Fixes

* **winprob:** increase win probability bar text contrast ([15a0e30](https://github.com/johnbr/mlb-live-scoreboard/commit/15a0e307ad0fe68db29f8219c676dedfe89436b5))
