function addAsset(projectAssetsInfo, asset) {
  const source = asset.Network;
  const category = asset.Category;

  projectAssetsInfo[source] = projectAssetsInfo[source] || {};
  projectAssetsInfo[source][category] = projectAssetsInfo[source][category] || [];

  try {
    if (asset.Logo) {
      const fileName = asset.Logo.split("/").slice(-1)[0];
      asset.Logo = `https://gcs.subscan.io/assets-info/logos/${fileName}`;
    }
  } catch {}

  projectAssetsInfo[source][category].push(asset);
}

module.exports = {
  addAsset,
};
