const assetsContext = require.context("./assets", false, /\.json$/);
const networks = require("./networks.json");
const { addAsset } = require("./asset-utils");
const projectAssetsInfo = {};

assetsContext.keys().forEach((k) => {
  const c = assetsContext(k);
  if (!networks.includes(c.Network)) {
    if (c.Network) {
      return;
    }
    // token info in bulk
    c.forEach((p) => {
      addAsset(projectAssetsInfo, p);
    });
  } else {
    addAsset(projectAssetsInfo, c);
  }

});
module.exports = {
  projectAssetsInfo,
}
