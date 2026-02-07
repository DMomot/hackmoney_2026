// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "../src/PremarketRouter.sol";

contract DeployRouter is Script {
    address constant LIFI_DIAMOND = 0x1231DEB6f5749EF6cE6943a275A1D3E7486F4EaE;

    function run() public {
        uint256 ownerKey = vm.envUint("OWNER_PRIVATE_KEY");
        vm.startBroadcast(ownerKey);

        PremarketRouter router = new PremarketRouter();
        router.setPlatformWallet(1, LIFI_DIAMOND);

        vm.stopBroadcast();

        console.log("Router deployed at:", address(router));
        console.log("LiFi Diamond set as platformId 1");
    }
}
