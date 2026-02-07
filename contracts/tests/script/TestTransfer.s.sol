// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "../src/PremarketRouter.sol";

interface IUSDC {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

contract TestTransfer is Script {
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant LIFI_DIAMOND = 0x1231DEB6f5749EF6cE6943a275A1D3E7486F4EaE;

    function run() public {
        address router = vm.envAddress("ROUTER_ADDRESS");
        uint256 userKey = vm.envUint("USER_PRIVATE_KEY");
        uint256 ownerKey = vm.envUint("OWNER_PRIVATE_KEY");
        address user = vm.addr(userKey);

        uint256 amount = 1 * 1e6; // 1 USDC

        console.log("User:", user);
        console.log("Router:", router);
        console.log("User USDC before:", IUSDC(USDC).balanceOf(user));
        console.log("LiFi USDC before:", IUSDC(USDC).balanceOf(LIFI_DIAMOND));

        // Step 1: User approves router
        vm.startBroadcast(userKey);
        IUSDC(USDC).approve(router, amount);
        vm.stopBroadcast();
        console.log("Approve done");

        // Step 2: Owner calls transferERC20 with metadata
        bytes memory metadata = abi.encode(
            "fifa-world-cup-2026",
            uint256(123456789),
            "yes"
        );

        vm.startBroadcast(ownerKey);
        PremarketRouter(router).transferERC20(USDC, user, 1, amount, metadata);
        vm.stopBroadcast();

        console.log("Transfer done");
        console.log("User USDC after:", IUSDC(USDC).balanceOf(user));
        console.log("LiFi USDC after:", IUSDC(USDC).balanceOf(LIFI_DIAMOND));
    }
}
