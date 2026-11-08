/* SPDX-License-Identifier: Apache-2.0 */
/* 원 wire bytes의 SHA-256. FIPS 180-4 §4.1.2/4.2.2/5/6.2를 직접 구현한다.
 * https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.180-4.pdf
 * 인증/권위나 canonical protobuf 변환을 하지 않는다. 외부 코드·heap·가변 전역은 없다. */
#ifndef XENOVA_DRIVE_SHA256_H
#define XENOVA_DRIVE_SHA256_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

static inline uint32_t xdr_sha256_rotate(uint32_t word,unsigned bits)
{
 /* 모든 내부 호출의 bits는 1..31 상수다. */
 return (word>>bits)|(word<<(32u-bits));
}
static inline void xdr_sha256_block(uint32_t hash[8],const uint8_t block[64])
{
 static const uint32_t round[64]={
  0x428a2f98u,0x71374491u,0xb5c0fbcfu,0xe9b5dba5u,0x3956c25bu,0x59f111f1u,0x923f82a4u,0xab1c5ed5u,
  0xd807aa98u,0x12835b01u,0x243185beu,0x550c7dc3u,0x72be5d74u,0x80deb1feu,0x9bdc06a7u,0xc19bf174u,
  0xe49b69c1u,0xefbe4786u,0x0fc19dc6u,0x240ca1ccu,0x2de92c6fu,0x4a7484aau,0x5cb0a9dcu,0x76f988dau,
  0x983e5152u,0xa831c66du,0xb00327c8u,0xbf597fc7u,0xc6e00bf3u,0xd5a79147u,0x06ca6351u,0x14292967u,
  0x27b70a85u,0x2e1b2138u,0x4d2c6dfcu,0x53380d13u,0x650a7354u,0x766a0abbu,0x81c2c92eu,0x92722c85u,
  0xa2bfe8a1u,0xa81a664bu,0xc24b8b70u,0xc76c51a3u,0xd192e819u,0xd6990624u,0xf40e3585u,0x106aa070u,
  0x19a4c116u,0x1e376c08u,0x2748774cu,0x34b0bcb5u,0x391c0cb3u,0x4ed8aa4au,0x5b9cca4fu,0x682e6ff3u,
  0x748f82eeu,0x78a5636fu,0x84c87814u,0x8cc70208u,0x90befffau,0xa4506cebu,0xbef9a3f7u,0xc67178f2u
 };
 uint32_t words[64];
 for(unsigned t=0;t<16;t++){
  unsigned i=4u*t;
  words[t]=((uint32_t)block[i]<<24)|((uint32_t)block[i+1]<<16)|
   ((uint32_t)block[i+2]<<8)|(uint32_t)block[i+3];
 }
 for(unsigned t=16;t<64;t++){
  uint32_t x=words[t-15],y=words[t-2];
  uint32_t small0=xdr_sha256_rotate(x,7)^xdr_sha256_rotate(x,18)^(x>>3);
  uint32_t small1=xdr_sha256_rotate(y,17)^xdr_sha256_rotate(y,19)^(y>>10);
  words[t]=words[t-16]+small0+words[t-7]+small1;
 }
 uint32_t a=hash[0],b=hash[1],c=hash[2],d=hash[3],e=hash[4],f=hash[5],g=hash[6],h=hash[7];
 for(unsigned t=0;t<64;t++){
  uint32_t large0=xdr_sha256_rotate(a,2)^xdr_sha256_rotate(a,13)^xdr_sha256_rotate(a,22);
  uint32_t large1=xdr_sha256_rotate(e,6)^xdr_sha256_rotate(e,11)^xdr_sha256_rotate(e,25);
  uint32_t choose=(e&f)^((~e)&g),majority=(a&b)^(a&c)^(b&c);
  uint32_t first=h+large1+choose+round[t]+words[t],second=large0+majority;
  h=g;g=f;f=e;e=d+first;d=c;c=b;b=a;a=first+second;
 }
 hash[0]+=a;hash[1]+=b;hash[2]+=c;hash[3]+=d;
 hash[4]+=e;hash[5]+=f;hash[6]+=g;hash[7]+=h;
}

/* data[0:length]를 수정하지 않고 digest32B를 쓴다. NULL+0은 빈 메시지다.
 * NULL out, NULL+nonzero, FIPS의 64bit bit-length 초과, 입력/출력 겹침은 false다.
 * 실패 때 out은 그대로다. 호출자는 유효한 읽기/쓰기 메모리를 제공해야 한다.
 * 배열 작업 공간은 caller 96B(hash32+tail64), block 256B다. 재귀·heap은 없으며
 * 메시지 크기에 따라 stack이 증가하지 않는다(실제 compiler frame은 별도 측정). */
static inline bool xdr_sha256(const void *data,size_t length,uint8_t out[32])
{
 if(!out||(!data&&length)||(uintmax_t)length>UINT64_MAX/8u)return false;
 if(length){
  uintptr_t begin=(uintptr_t)data,target=(uintptr_t)out;
  if((target>=begin&&target-begin<length)||(begin>target&&begin-target<32u))return false;
 }
 const uint8_t *input=(const uint8_t *)data;
 uint32_t hash[8]={0x6a09e667u,0xbb67ae85u,0x3c6ef372u,0xa54ff53au,
  0x510e527fu,0x9b05688cu,0x1f83d9abu,0x5be0cd19u};
 size_t remaining=length;
 while(remaining>=64u){xdr_sha256_block(hash,input);input+=64;remaining-=64;}
 uint8_t tail[64]={0};
 for(size_t i=0;i<remaining;i++)tail[i]=input[i];
 tail[remaining]=0x80u;
 if(remaining>=56u){
  xdr_sha256_block(hash,tail);
  for(unsigned i=0;i<64;i++)tail[i]=0;
 }
 uint64_t bits=(uint64_t)length*8u;
 for(unsigned i=0;i<8;i++)tail[63u-i]=(uint8_t)(bits>>(8u*i));
 xdr_sha256_block(hash,tail);
 for(unsigned i=0;i<8;i++)for(unsigned j=0;j<4;j++)out[4u*i+j]=(uint8_t)(hash[i]>>(24u-8u*j));
 return true;
}
#endif
