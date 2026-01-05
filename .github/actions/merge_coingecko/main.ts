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

// 通用数组去重工具函数（保留元素顺序，无重复项）
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

// 核心：生成【Symbol+HistorySlug】双字段联合唯一键
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


// 最终合并函数（双主键匹配+深度合并+顺序保留）
const mergeAssetArrays = (a: AssetItem[], b: AssetItem[]): AssetItem[] => {
  const assetMap = new Map<string, AssetItem>();

  // 第一步：存入数组a的所有项，生成双主键索引
  a.forEach(item => {
    const unionKey = generateUnionKey(item.Symbol, item.HistorySlug);
    assetMap.set(unionKey, { ...item });
  });

  // 第二步：遍历数组b，执行双主键匹配+合并逻辑
  b.forEach(item => {
    const unionKey = generateUnionKey(item.Symbol, item.HistorySlug);
    if (assetMap.has(unionKey)) {
      // a数组的quote不参与合并，直接使用b数组的quote（如果b数组quote为空则结果也为空）
      assetMap.set(unionKey, {
        ...item,
        quote: item.quote
      });
    } else {
      assetMap.set(unionKey, { ...item });
    }
  });

  // 第三步：严格保留最终顺序 → b数组项在前 + a数组独有项在后
  const result: AssetItem[] = [];
  b.forEach(item => {
    const unionKey = generateUnionKey(item.Symbol, item.HistorySlug);
    const target = assetMap.get(unionKey);
    if (target) {
      result.push(target);
      assetMap.delete(unionKey); // 防止重复添加
    }
  });

  // 再添加a数组中，b数组没有的独有项（保持a自身原有顺序）
  a.forEach(item => {
    const unionKey = generateUnionKey(item.Symbol, item.HistorySlug);
    const target = assetMap.get(unionKey);
    if (target) {
      result.push(target);
    }
  });

  return result;
};

// 带超时的 fetch 请求
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
    // 重新抛出其他错误，包含更多上下文信息
    const errorMessage = error.message || error.toString();
    throw new Error(`Fetch failed: ${errorMessage}`);
  }
};

// 带重试的请求函数
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
        const delay = Math.min(1000 * Math.pow(2, attempt - 1), 10000); // 指数退避，最大10秒
        actions.info(`Retrying in ${delay}ms...`);
        await new Promise(resolve => setTimeout(resolve, delay));
      }
    }
  }
  
  throw new Error(`Failed after ${maxRetries} attempts: ${lastError?.message || 'Unknown error'}`);
};

// 检查coingecko token是否存在, 如果不存在则移除
const filterCoingeckoToken = async (combinedData: AssetItem[], coingeckoToken: string): Promise<AssetItem[]> => {
  // 获取所有的tokenid
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
    const response = await fetchWithRetry(coingeckoUrl, { headers }, 3, 60000); // 60秒超时，最多重试3次
    const coingeckoData = await response.json();
    const validIds = new Set(coingeckoData.map((item: any) => item.id));
    // 需要打印被过滤的tokenid
    for (const data of combinedData) {
      if (!validIds.has(data.HistorySlug)) {
        actions.info(`Filtered tokenid: ${data.HistorySlug}`);
      }
    }
    return combinedData.filter(item => validIds.has(item.HistorySlug));
  } catch (error: any) {
    actions.error(`Failed to fetch coingecko data: ${error.message}`);
    actions.warning("Returning original data without filtering due to fetch error");
    return combinedData; // 如果请求失败，返回原始数据而不是抛出错误
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

  // 检查文件是否存在
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

