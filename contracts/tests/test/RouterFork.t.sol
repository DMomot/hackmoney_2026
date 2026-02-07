// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/PremarketRouter.sol";

interface IUSDC {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
}

contract RouterForkTest is Test {
    uint256 constant PK_OWNER = 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80;
    uint256 constant PK_USER  = 0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d;

    address owner;
    address user;

    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant USDC_WHALE = 0x8da91A6298eA5d1A8Bc985e99798fd0A0f05701a;
    address constant LIFI_DIAMOND = 0x1231DEB6f5749EF6cE6943a275A1D3E7486F4EaE;

    PremarketRouter router;

    function setUp() public {
        owner = vm.addr(PK_OWNER);
        user  = vm.addr(PK_USER);

        vm.deal(owner, 10 ether);
        vm.deal(user, 10 ether);

        vm.prank(owner);
        router = new PremarketRouter();

        // Give user 10 USDC
        vm.prank(USDC_WHALE);
        IUSDC(USDC).transfer(user, 10 * 1e6);
    }

    function test_bridgeViaLiFi() public {
        uint256 amount = 1 * 1e6; // 1 USDC

        // User approves router
        vm.prank(user);
        IUSDC(USDC).approve(address(router), amount);

        // Get LiFi calldata via FFI (curl)
        string[] memory cmd = new string[](3);
        cmd[0] = "bash";
        cmd[1] = "-c";
        cmd[2] = string(abi.encodePacked(
            "curl -s 'https://li.quest/v1/quote?fromChain=8453&toChain=137",
            "&fromToken=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "&toToken=0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
            "&fromAmount=1000000",
            "&fromAddress=", vm.toString(address(router)),
            "&toAddress=", vm.toString(user),
            "&slippage=0.05' | python3 -c \"import sys,json; d=json.load(sys.stdin); print(d['transactionRequest']['data'])\""
        ));
        bytes memory lifiData = vm.ffi(cmd);
        // FFI returns with trailing newline, trim it
        lifiData = _trimNewline(lifiData);

        require(lifiData.length > 4, "empty lifi data");

        uint256 routerBalBefore = IUSDC(USDC).balanceOf(address(router));
        uint256 userBalBefore = IUSDC(USDC).balanceOf(user);

        bytes memory metadata = abi.encode("test-bridge", uint256(1), "yes");

        // Owner calls bridgeViaLiFi
        vm.prank(owner);
        router.bridgeViaLiFi(
            USDC,
            user,
            amount,
            LIFI_DIAMOND,
            lifiData,
            metadata
        );

        // After bridge: user lost 1 USDC, router should have 0 (tokens went to LiFi)
        uint256 userBalAfter = IUSDC(USDC).balanceOf(user);
        assertEq(userBalBefore - userBalAfter, amount, "user should lose 1 USDC");

        uint256 routerBalAfter = IUSDC(USDC).balanceOf(address(router));
        assertEq(routerBalAfter, 0, "router should have 0 USDC (sent to LiFi)");

        console.log("Bridge call succeeded. USDC sent to LiFi for bridging to Polygon.");
    }

    function _trimNewline(bytes memory data) internal pure returns (bytes memory) {
        uint256 len = data.length;
        while (len > 0 && (data[len - 1] == 0x0a || data[len - 1] == 0x0d)) {
            len--;
        }
        bytes memory trimmed = new bytes(len);
        for (uint256 i = 0; i < len; i++) {
            trimmed[i] = data[i];
        }
        return trimmed;
    }
}
