# Changelog

## [1.25.3](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.25.2...v1.25.3) (2026-08-04)


### Bug Fixes

* restore ESPN fetches rejected with HTTP 403 by Akamai ([#90](https://github.com/johnbr/mlb-live-scoreboard/issues/90)) ([6cc78ee](https://github.com/johnbr/mlb-live-scoreboard/commit/6cc78eeef3a6c3094a3b52a3628d59fec8b6b913))

## [1.25.2](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.25.1...v1.25.2) (2026-07-31)


### Bug Fixes

* cut a release so the docs updates reach HACS users ([82ed338](https://github.com/johnbr/mlb-live-scoreboard/commit/82ed3382b03e6c2b485387bed7302b2ceda05485))

## [1.25.1](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.25.0...v1.25.1) (2026-07-15)


### Bug Fixes

* show season AVG/ERA (not game values) on the All-Star card ([#86](https://github.com/johnbr/mlb-live-scoreboard/issues/86)) ([5030002](https://github.com/johnbr/mlb-live-scoreboard/commit/503000282defcb141c578a5ae92776438ebba384))

## [1.25.0](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.24.0...v1.25.0) (2026-07-15)


### Features

* auto-display the All-Star Game on every card on game day ([2bb9fd8](https://github.com/johnbr/mlb-live-scoreboard/commit/2bb9fd8f963cf35af07dbe0284db290271d35eb0))

## [1.24.0](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.23.5...v1.24.0) (2026-07-02)


### Features

* page back through past innings' play-by-play (live card) ([b296292](https://github.com/johnbr/mlb-live-scoreboard/commit/b296292ac3d4042263bc7f2a90ab2004ed2de380))

## [1.23.5](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.23.4...v1.23.5) (2026-07-02)


### Bug Fixes

* **card:** repair render fingerprint and refresh_rate, escape ESPN strings, scope stylesheet to the card element ([#81](https://github.com/johnbr/mlb-live-scoreboard/issues/81)) ([5baa2f0](https://github.com/johnbr/mlb-live-scoreboard/commit/5baa2f0a85f94fee93ff721ab26a71ea3c708dcd))
* **config_flow:** stop assigning OptionsFlow.config_entry explicitly ([#81](https://github.com/johnbr/mlb-live-scoreboard/issues/81)) ([c2eda25](https://github.com/johnbr/mlb-live-scoreboard/commit/c2eda251d114d43c35f98f9ad6467b8d782810dd))


### Performance Improvements

* **coordinator:** adapt poll cadence to game state and cut redundant per-refresh work ([#81](https://github.com/johnbr/mlb-live-scoreboard/issues/81)) ([db62481](https://github.com/johnbr/mlb-live-scoreboard/commit/db6248191ad460665bf28cd90cfcf20f7e036c09))

## [1.23.4](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.23.3...v1.23.4) (2026-06-28)


### Bug Fixes

* **coordinator:** suppress impossible score jumps from stale baselines ([#78](https://github.com/johnbr/mlb-live-scoreboard/issues/78)) ([374fcca](https://github.com/johnbr/mlb-live-scoreboard/commit/374fccaa16c0afd5c752dbef74ec151cdffb553e))

## [1.23.3](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.23.2...v1.23.3) (2026-06-28)


### Bug Fixes

* **coordinator:** clear prior half's play-by-play at a same-inning half flip ([a106b9c](https://github.com/johnbr/mlb-live-scoreboard/commit/a106b9c8ca9ec576f5dd5b1357ba84f2af109a1f))

## [1.23.2](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.23.1...v1.23.2) (2026-06-27)


### Bug Fixes

* **card:** stop showing an outs badge on non-out plays  ([7d0e3cc](https://github.com/johnbr/mlb-live-scoreboard/commit/7d0e3cc045e56031c216f292c6f7d6ec9780d483))

## [1.23.1](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.23.0...v1.23.1) (2026-06-27)


### Bug Fixes

* **coordinator:** suppress duplicate game-lifecycle events on status flicker ([f5bd18f](https://github.com/johnbr/mlb-live-scoreboard/commit/f5bd18f284559b9049a3f58d548dfc6b1d1a0908))

## [1.23.0](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.22.6...v1.23.0) (2026-06-18)


### Features

* **card:** prev/next schedule navigation arrows on the non-live card ([#66](https://github.com/johnbr/mlb-live-scoreboard/issues/66)) ([55e52f7](https://github.com/johnbr/mlb-live-scoreboard/commit/55e52f7fea55502627c81975c9d336bbccd83ff0))

## [1.22.6](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.22.5...v1.22.6) (2026-06-18)


### Bug Fixes

* **recorder:** exclude live-game attributes from recorder history ([#69](https://github.com/johnbr/mlb-live-scoreboard/issues/69)) ([34d4cd9](https://github.com/johnbr/mlb-live-scoreboard/commit/34d4cd9f14873383464055f5100dfe9802fca312))

## [1.22.5](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.22.4...v1.22.5) (2026-06-17)


### Bug Fixes

* **coordinator:** don't surface a two-way player's pitching line as batter stats ([#67](https://github.com/johnbr/mlb-live-scoreboard/issues/67)) ([bae7f7c](https://github.com/johnbr/mlb-live-scoreboard/commit/bae7f7c7440a04de43f890a86e62b54857dd913c))

## [1.22.4](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.22.3...v1.22.4) (2026-06-10)


### Bug Fixes

* expose game_active state attribute on scoreboard sensor ([#64](https://github.com/johnbr/mlb-live-scoreboard/issues/64)) ([dab2969](https://github.com/johnbr/mlb-live-scoreboard/commit/dab2969b4fbfc992bf5e84dac4e169f9e185ffb6))

## [1.22.3](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.22.2...v1.22.3) (2026-06-07)


### Bug Fixes

* **card:** highlight triple-digit pitch velocities in red ([7d8c4d6](https://github.com/johnbr/mlb-live-scoreboard/commit/7d8c4d6c1397093e92c9e37305befe58e8712445))

## [1.22.2](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.22.1...v1.22.2) (2026-06-07)


### Bug Fixes

* **card:** keep base diamond square when center column shrinks ([b9c8873](https://github.com/johnbr/mlb-live-scoreboard/commit/b9c8873bd8fccaa0ab21215858c350fdaa2b1e35))

## [1.22.1](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.22.0...v1.22.1) (2026-06-03)


### Bug Fixes

* **coordinator:** on-deck shows active sub, not subbed-out starter ([015474c](https://github.com/johnbr/mlb-live-scoreboard/commit/015474c5a0f0d5afdde6c8b70a12808765c88869))

## [1.22.0](https://github.com/johnbr/mlb-live-scoreboard/compare/v1.21.0...v1.22.0) (2026-06-01)


### Features

* **card:** opt-in pitch-zone graphic under the base diamond ([6c08591](https://github.com/johnbr/mlb-live-scoreboard/commit/6c08591f128140a9d2f7cb8f7006f6ec8ecffeaa))

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
