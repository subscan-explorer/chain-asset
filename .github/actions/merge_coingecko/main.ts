const actions = require("@actions/core");
const path = require("path");
const fs = require("fs");
// @ts-ignore - node-fetch v2 types
const nodeFetch = require("node-fetch");


interface AssetItem {
  Symbol: string;
  HistorySlug: string;
  quote: string[];
}

// Generic array deduplication utility function (preserves element order, no duplicates)
const uniqueArray = <T>(arr: T[]): T[] => {
  const set = new Set<T>();
  return arr.filter(item => {
    if (!set.has(item)) {
      set.add(item);
      return true;
    }
    return false;
  });
};

// Core: Generate union unique key from Symbol+HistorySlug composite fields
const generateUnionKey = (symbol: string, historySlug: string): string => {
  return `${historySlug}`;
};

function processJsonFiles(directoryPath: string): Array<AssetItem> {
  const result: Array<AssetItem> = [];
  
  const files = fs.readdirSync(directoryPath);
  const coingeckoTokenObject = {};
  for (const file of files) {
    if (file === "template.json"){
      continue
    }
    if (path.extname(file).toLowerCase() === '.json') {
      const filePath = path.join(directoryPath, file);
      const fileContent = fs.readFileSync(filePath, 'utf-8');
      
      try {
        let jsonData = JSON.parse(fileContent);
        if (!Array.isArray(jsonData)) {
          jsonData = [jsonData];
        }
        for (const data of jsonData) {
          if (!data.TokenSymbol || !data["Coingecko API ID"]) {
            continue;
          }
          const key = data.TokenSymbol + "_" + data["Coingecko API ID"];
          if (coingeckoTokenObject[key]) {
            coingeckoTokenObject[key].push(data);
          } else {
            coingeckoTokenObject[key] = [data];
          }
        }
      } catch (error) {
        actions.setFailed(`Error processing file ${file}:`, error);
      }
    }
  }

  for (const key in coingeckoTokenObject) {
     const item: AssetItem = {
      Symbol: key.split("_")[0],
      HistorySlug: key.split("_")[1],
      quote: coingeckoTokenObject[key].map(quote => `${quote.TokenID}:${quote.Network}`)
     };
     result.push(item);
  }
  return result;
}


// Final merge function (composite key matching + deep merge + order preservation)
const mergeAssetArrays = (a: AssetItem[], b: AssetItem[]): AssetItem[] => {
  const assetMap = new Map<string, AssetItem>();

  // Step 1: Store all items from array a, generate composite key index
  a.forEach(item => {
    const unionKey = generateUnionKey(item.Symbol, item.HistorySlug);
    assetMap.set(unionKey, { ...item });
  });

  // Step 2: Iterate through array b, execute composite key matching + merge logic
  b.forEach(item => {
    const unionKey = generateUnionKey(item.Symbol, item.HistorySlug);
    if (assetMap.has(unionKey)) {
      // Array a's quote is not merged, directly use array b's quote (if array b's quote is empty, result is also empty)
      assetMap.set(unionKey, {
        ...item,
        quote: item.quote
      });
    } else {
      assetMap.set(unionKey, { ...item });
    }
  });

  // Step 3: Strictly preserve final order → array b items first + array a unique items last
  const result: AssetItem[] = [];
  b.forEach(item => {
    const unionKey = generateUnionKey(item.Symbol, item.HistorySlug);
    const target = assetMap.get(unionKey);
    if (target) {
      result.push(target);
      assetMap.delete(unionKey); // Prevent duplicate additions
    }
  });

  // Add unique items from array a that are not in array b (preserve array a's original order)
  a.forEach(item => {
    const unionKey = generateUnionKey(item.Symbol, item.HistorySlug);
    const target = assetMap.get(unionKey);
    if (target) {
      result.push(target);
    }
  });

  return result;
};

// Fetch request with timeout
const fetchWithTimeout = async (url: string, options: any, timeoutMs: number = 30000): Promise<any> => {
  const timeoutPromise = new Promise<never>((_, reject) => {
    setTimeout(() => {
      reject(new Error(`Request timeout after ${timeoutMs}ms`));
    }, timeoutMs);
  });
  
  try {
    const fetchPromise = nodeFetch(url, options);
    const response = await Promise.race([fetchPromise, timeoutPromise]);
    return response;
  } catch (error: any) {
    if (error.message && error.message.includes('timeout')) {
      throw error;
    }
    // Re-throw other errors with additional context information
    const errorMessage = error.message || error.toString();
    throw new Error(`Fetch failed: ${errorMessage}`);
  }
};

// Request function with retry mechanism
const fetchWithRetry = async (url: string, options: any, maxRetries: number = 3, timeoutMs: number = 30000): Promise<any> => {
  let lastError: Error | null = null;
  
  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      actions.info(`Fetching coingecko data (attempt ${attempt}/${maxRetries})...`);
      const response = await fetchWithTimeout(url, options, timeoutMs);
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      return response;
    } catch (error: any) {
      lastError = error;
      actions.warning(`Attempt ${attempt} failed: ${error.message}`);
      
      if (attempt < maxRetries) {
        const delay = Math.min(1000 * Math.pow(2, attempt - 1), 10000); // Exponential backoff, maximum 10 seconds
        actions.info(`Retrying in ${delay}ms...`);
        await new Promise(resolve => setTimeout(resolve, delay));
      }
    }
  }
  
  throw new Error(`Failed after ${maxRetries} attempts: ${lastError?.message || 'Unknown error'}`);
};

// Check if coingecko token exists, remove if not present
const filterCoingeckoToken = async (combinedData: AssetItem[], coingeckoToken: string): Promise<AssetItem[]> => {
  // Get all token IDs
  const tokenIds = [];
  for (const data of combinedData) {
    tokenIds.push(data.HistorySlug);
  }
  if (tokenIds.length === 0) {
    return combinedData;
  }
  const coingeckoUrl = `https://pro-api.coingecko.com/api/v3/coins/markets?vs_currency=USD&ids=${tokenIds.join(",")}`;
  const headers = {
    "x-cg-pro-api-key": coingeckoToken,
    "Content-Type": "application/json"
  }
  actions.info(`Checking coingecko token: ${coingeckoUrl}`);
  
  try {
    const response = await fetchWithRetry(coingeckoUrl, { headers }, 3, 60000); // 60 second timeout, maximum 3 retries
    const coingeckoData = await response.json();
    const validIds = new Set(coingeckoData.map((item: any) => item.id));
    // Need to print filtered token IDs
    for (const data of combinedData) {
      if (!validIds.has(data.HistorySlug)) {
        actions.info(`Filtered tokenid: ${data.HistorySlug}`);
      }
    }
    return combinedData.filter(item => validIds.has(item.HistorySlug));
  } catch (error: any) {
    actions.error(`Failed to fetch coingecko data: ${error.message}`);
    actions.warning("Returning original data without filtering due to fetch error");
    return combinedData; // If request fails, return original data instead of throwing error
  }
}

async function main() {  
  const coingeckoToken = actions.getInput("coingecko_token");
  if (!coingeckoToken) {
    actions.setFailed("Coingecko token is required");
    return ;
  }
  const assetsDir = "./assets";
  const outputFile = "./combined_output_coingecko.json";

  const kubernetesManifestsBaseCoingeckoJsonPath = "./kubernetes-manifests/subscan/networks/coingecko-token.json";

  // Check if file exists
  if (!fs.existsSync(kubernetesManifestsBaseCoingeckoJsonPath)) {
    actions.setFailed(`File ${kubernetesManifestsBaseCoingeckoJsonPath} does not exist`);
    return;
  }
  
  const combinedData = processJsonFiles(assetsDir);

  
  const baseCoingeckoJson: Array<AssetItem> = JSON.parse(fs.readFileSync(kubernetesManifestsBaseCoingeckoJsonPath, 'utf-8'));


  const mergedData = mergeAssetArrays(baseCoingeckoJson, combinedData);
  const filteredData = await filterCoingeckoToken(mergedData, coingeckoToken);
  if (filteredData.length === 0) {
    actions.setFailed("No coingecko token data found");
    return;
  }

  fs.writeFileSync(outputFile, JSON.stringify(filteredData, null, 2));
  actions.setOutput("json_filename", outputFile)
  console.log(`Combined JSON data has been written to ${outputFile}`);
  return ;
}

main().then();

